import json
import boto3
from datetime import datetime, timedelta
import os



AWS_REGION = 'us-west-1'  

ec2_client = boto3.client('ec2', region_name=AWS_REGION)           
cloudwatch = boto3.client('cloudwatch', region_name=AWS_REGION)     
s3_client = boto3.client('s3', region_name=AWS_REGION)             
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)       
sns_client = boto3.client('sns', region_name=AWS_REGION)
elbv2_client = boto3.client('elbv2', region_name=AWS_REGION)  
elb_client = boto3.client('elb', region_name=AWS_REGION)      
try:
    rds_client = boto3.client('rds', region_name=AWS_REGION)
except:
    rds_client = None
cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')

S3_BUCKET = os.getenv('S3_BUCKET_NAME') 

DYNAMODB_TABLE = os.getenv('DYNAMODB_TABLE')

SNS_TOPIC_ARN = os.getenv('SNS_TOPIC_ARN') 


# Grace period configuration
GRACE_PERIOD_DAYS = 0  # Days to wait before deletion (0 = immediate, 7 = one week)
SKIP_QUARANTINE = True  # True = skip stopping, delete immediately after 3 idle checks

# Idle detection threshold
IDLE_CHECKS_BEFORE_ACTION = 1  # Number of consecutive idle checks before action
CPU_IDLE_THRESHOLD = 5.0  # If CPU < 5% for 24 hours = idle

# NAT Gateway monitoring
ENABLE_NAT_GATEWAY_MONITORING = True  # Set to False to disable
NAT_GATEWAY_IDLE_THRESHOLD_MB = 1.0   # MB transferred in 7 days (< 1 MB = idle)
NAT_GATEWAY_IDLE_CHECKS = 1          # Consecutive idle checks before deletion
NAT_GATEWAY_RELEASE_EIP = True        # Release Elastic IP when deleting NAT Gateway

# RDS monitoring configuration
ENABLE_RDS_MONITORING = True          # Set to False to disable
RDS_IDLE_CHECKS = 1                  # Consecutive checks before action (3 = 3 days with daily checks)
RDS_GRACE_PERIOD_DAYS = 7             # Days to wait after stopping before deletion
RDS_MIN_CONNECTIONS_THRESHOLD = 1.0   # Avg connections per day to be considered active
RDS_MIN_IOPS_THRESHOLD = 100          # Total IOPS over 7 days to be considered active

# S3 monitoring configuration
ENABLE_S3_MONITORING = True                    # Set to False to disable
S3_EMPTY_BUCKET_CHECKS = 1                    # Consecutive empty checks before deletion
S3_APPLY_LIFECYCLE_AUTO = True                 # Auto-apply lifecycle to buckets without one
S3_LIFECYCLE_TRANSITION_1_DAYS = 30            # Days to Standard-IA
S3_LIFECYCLE_TRANSITION_2_DAYS = 180           # Days to Glacier Instant

# EBS monitoring configuration
ENABLE_EBS_MONITORING = True              # Set to False to disable
EBS_UNATTACHED_CHECKS = 1                 # Consecutive checks before deletion (3 = 3 days)
EBS_CREATE_SNAPSHOT_BEFORE_DELETE = True  # Always create snapshot before deleting

# Load Balancer monitoring configuration
ENABLE_LB_MONITORING = True               # Set to False to disable
LB_IDLE_CHECKS = 1                       # Consecutive idle checks before deletion
LB_MIN_HEALTHY_TARGETS = 1                # If 0 healthy targets = idle
LB_MIN_CONNECTIONS_THRESHOLD = 10         # Min connections in 7 days to be considered active
LB_MIN_BYTES_THRESHOLD = 1000000          # Min bytes (1 MB) in 7 days to be considered active

# VPC/Subnet cleanup configuration
ENABLE_VPC_CLEANUP = True                 # Set to False to disable
VPC_IDLE_CHECKS = 1                       # Consecutive idle checks before deletion
VPC_MIN_AGE_DAYS = 7                      # VPC must be at least 7 days old before deletion
DELETE_EMPTY_VPCS = True                  # Delete VPCs with no resources
DELETE_ORPHANED_SUBNETS = True            # Delete subnets with no resources


def send_cloudwatch_metric(metric_name, value, unit='Count'):
    
    try:
        cloudwatch.put_metric_data(
            Namespace='CostGuardian',
            MetricData=[
                {
                    'MetricName': metric_name,
                    'Value': value,
                    'Unit': unit,
                    'Timestamp': datetime.now()
                }
            ]
        )
        print(f"   Sent metric: {metric_name} = {value}")
    except Exception as e:
        print(f"    Failed to send metric {metric_name}: {str(e)}")



def lambda_handler(event, context):
    
    validate_configuration()
    print(" CostGuardian EC2 Monitor started...")
    
    # Get reference to DynamoDB table
    table = dynamodb.Table(DYNAMODB_TABLE)
    
    # Track statistics
    stats = {
        'total_instances': 0,
        'active_instances': 0,
        'idle_instances': 0,
        'backed_up': 0,
        'errors': 0
    }
    
    try:
       
        print(" Fetching all EC2 instances...")
    
        response = ec2_client.describe_instances()
    
        all_instances = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                all_instances.append(instance)
        
        stats['total_instances'] = len(all_instances)
        print(f"Found {len(all_instances)} instances")
        
        
        # If no instances, will exit
        if len(all_instances) == 0:
            print(" No EC2 instances found. Nothing to monitor!")
        else:
         
        # STEP 2: CHECK EACH INSTANCE (STATE MACHINE)
         
            for instance in all_instances:
                instance_id = instance['InstanceId']
                instance_state = instance['State']['Name']
                
                print(f"\n Checking instance: {instance_id} (State: {instance_state})")
                
                # Skip terminated instances
                if instance_state == 'terminated':
                    print(f"    Skipping (already terminated)")
                    continue
                
                # Check for protection tag
                tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                if tags.get('CostGuardian') == 'Ignore':
                    print(f"    Skipping (protected with CostGuardian=Ignore tag)")
                    continue
                
                try:
                    # Get historical data for this resource
                    history = get_resource_history(table, instance_id, days=30)
                    
                    # Get current CPU metrics (only if running)
                    if instance_state == 'running':
                        cpu_usage = get_cpu_utilization(instance_id)
                        print(f"   Average CPU (24h): {cpu_usage:.2f}%")
                    else:
                        # Instance is stopped - treat as idle
                        cpu_usage = 0.0
                        print(f"   Instance is {instance_state}, treating as idle (CPU: 0%)")
                    
                    # Determine what action to take based on history
                    action, idle_count, quarantine_date = determine_action(history, cpu_usage)
                    print(f"   Recommended action: {action} (idle count: {idle_count})")
                    
                     
                    # EXECUTE ACTION BASED ON STATE MACHINE
                     
                    
                    if action == 'ACTIVE':
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        # STATE: ACTIVE
                        # Instance is being used, just log it
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        print(f"   Instance is ACTIVE (CPU >= {CPU_IDLE_THRESHOLD}%)")
                        stats['active_instances'] += 1
                        
                        table.put_item(Item={
                            'ResourceId': instance_id,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'EC2',
                            'Status': 'ACTIVE',
                            'CpuUsage': str(cpu_usage),
                            'LastChecked': datetime.now().isoformat(),
                            'InstanceType': instance.get('InstanceType'),
                            'InstanceName': tags.get('Name', 'Unnamed')
                        })
                    
                    elif action == 'WARN':
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        # STATE: IDLE_WARNING
                        # First or second idle detection
                        # Action: Backup config + send warning email
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        print(f"    IDLE WARNING (detection #{idle_count + 1})")
                        stats['idle_instances'] += 1
                        
                        # Create backup
                        backup_success = backup_instance_config(instance, instance_id)
                        if backup_success:
                            stats['backed_up'] += 1
                        
                        # Log to DynamoDB
                        table.put_item(Item={
                            'ResourceId': instance_id,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'EC2',
                            'Status': 'IDLE_WARNING',
                            'CpuUsage': str(cpu_usage),
                            'LastChecked': datetime.now().isoformat(),
                            'ConfigBackupPath': f's3://{S3_BUCKET}/ec2-configs/{instance_id}/',
                            'IdleCount': idle_count + 1,
                            'InstanceType': instance.get('InstanceType'),
                            'InstanceName': tags.get('Name', 'Unnamed')
                        })
                        
                        # Send warning email (only on first detection)
                        if idle_count == 0:
                            send_idle_alert(instance, instance_id, cpu_usage)
                    
                    elif action == 'QUARANTINE':
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        # STATE: QUARANTINE
                        # Idle for 3+ checks
                        # Action: Stop instance + create AMI
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        print(f"   QUARANTINE STATE (idle for {idle_count} checks)")
                        stats['idle_instances'] += 1
                        
                        # Only stop if currently running
                        if instance_state == 'running':
                            print(f"   Instance is RUNNING - initiating STOP sequence")
                            
                            # Final backup before stopping
                            backup_success = backup_instance_config(instance, instance_id)
                            
                            # Stop instance and create AMI
                            ami_id = stop_instance(instance_id, instance)
                            
                            if ami_id:
                                # Log quarantine action
                                table.put_item(Item={
                                    'ResourceId': instance_id,
                                    'Timestamp': int(datetime.now().timestamp()),
                                    'ResourceType': 'EC2',
                                    'Status': 'QUARANTINE',
                                    'CpuUsage': str(cpu_usage),
                                    'LastChecked': datetime.now().isoformat(),
                                    'ConfigBackupPath': f's3://{S3_BUCKET}/ec2-configs/{instance_id}/',
                                    'AMI_Backup': ami_id,
                                    'QuarantineDate': datetime.now().isoformat(),
                                    'IdleCount': idle_count,
                                    'InstanceType': instance.get('InstanceType'),
                                    'InstanceName': tags.get('Name', 'Unnamed')
                                })
                                
                                # Send quarantine email
                                send_quarantine_alert(instance, instance_id, ami_id, idle_count)
                            else:
                                print(f"   Failed to stop instance - will retry next check")
                                stats['errors'] += 1
                        else:
                            # Instance already stopped
                            print(f"   Instance already stopped - monitoring grace period")
                            
                            # Just log the check
                            table.put_item(Item={
                                'ResourceId': instance_id,
                                'Timestamp': int(datetime.now().timestamp()),
                                'ResourceType': 'EC2',
                                'Status': 'QUARANTINE',
                                'CpuUsage': str(cpu_usage),
                                'LastChecked': datetime.now().isoformat(),
                                'QuarantineDate': quarantine_date,
                                'IdleCount': idle_count,
                                'InstanceType': instance.get('InstanceType'),
                                'InstanceName': tags.get('Name', 'Unnamed')
                            })
                    
                    elif action == 'DELETE':
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        # STATE: DELETE
                        # Grace period expired (7+ days stopped)
                        # Action: Terminate instance + cleanup SGs
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        print(f"   DELETE STATE (quarantined for 7+ days)")
                        
                        # Verify backups exist before deleting
                        s3_key_prefix = f'ec2-configs/{instance_id}/'
                        
                        try:
                            s3_response = s3_client.list_objects_v2(
                                Bucket=S3_BUCKET,
                                Prefix=s3_key_prefix,
                                MaxKeys=1
                            )
                            
                            if s3_response.get('KeyCount', 0) > 0:
                                print(f"   S3 backups verified - safe to terminate")
                                
                                # Step 1: Terminate the instance
                                terminate_success = terminate_instance(instance_id)
                                
                                if terminate_success:
                                    # Step 2: Wait a moment for termination to process
                                    import time
                                    print(f"   Waiting 5 seconds for termination to complete...")
                                    time.sleep(5)
                                    
                                    # Step 3: Clean up security groups
                                    print(f"   Cleaning up associated security groups...")
                                    deleted_sgs = delete_security_groups(instance)
                                    
                                    # Log deletion with SG cleanup info
                                    table.put_item(Item={
                                        'ResourceId': instance_id,
                                        'Timestamp': int(datetime.now().timestamp()),
                                        'ResourceType': 'EC2',
                                        'Status': 'DELETED',
                                        'LastChecked': datetime.now().isoformat(),
                                        'ConfigBackupPath': f's3://{S3_BUCKET}/ec2-configs/{instance_id}/',
                                        'DeletedDate': datetime.now().isoformat(),
                                        'QuarantineDate': quarantine_date,
                                        'InstanceType': instance.get('InstanceType'),
                                        'InstanceName': tags.get('Name', 'Unnamed'),
                                        'EstimatedMonthlySavings': get_instance_cost(instance.get('InstanceType')),
                                        'DeletedSecurityGroups': str(deleted_sgs)  # Store as string
                                    })
                                    
                                    # Send deletion confirmation email with SG info
                                    send_deletion_alert(instance, instance_id, deleted_sgs)
                                    
                                    print(f"  Instance and associated resources cleaned up successfully")
                                else:
                                    print(f"   Termination failed - will retry next check")
                                    stats['errors'] += 1
                            else:
                                print(f"   ERROR: No S3 backups found!")
                                print(f"    SAFETY: Refusing to delete without backups")
                                print(f"    Skipping deletion - will check backup status next time")
                                stats['errors'] += 1
                        
                        except Exception as e:
                            print(f"   Error verifying backups: {str(e)}")
                            print(f"    SAFETY: Refusing to delete due to verification error")
                            stats['errors'] += 1
                
                except Exception as e:
                    print(f"   Error processing instance {instance_id}: {str(e)}")
                    import traceback
                    print(f"   Traceback: {traceback.format_exc()}")
                    stats['errors'] += 1
                
         
        # STEP 3: CHECK NAT GATEWAYS
         
        print(f"\n" + "="*50)
        print(f" NAT GATEWAY MONITORING")
        print("="*50)
        
        # Get all NAT Gateways
        nat_gateways = get_all_nat_gateways()
        
        nat_stats = {
            'total': len(nat_gateways),
            'active': 0,
            'idle': 0,
            'deleted': 0,
            'errors': 0
        }
        
        for nat_gw in nat_gateways:
            nat_gw_id = nat_gw['NatGatewayId']
            
            print(f"\n Checking NAT Gateway: {nat_gw_id}")
            
            # Check for protection tag
            tags = {tag['Key']: tag['Value'] for tag in nat_gw.get('Tags', [])}
            if tags.get('CostGuardian') == 'Ignore':
                print(f"    Skipping (protected with CostGuardian=Ignore tag)")
                continue
            
            try:
                # Check usage metrics
                bytes_out, bytes_in, is_idle = check_nat_gateway_usage(nat_gw_id)
                
                # Get history from DynamoDB
                history = get_resource_history(table, nat_gw_id, days=30)
                
                # Count consecutive idle checks
                idle_count = 0
                for entry in reversed(history):
                    if entry.get('Status') in ['IDLE', 'IDLE_WARNING']:
                        idle_count += 1
                    elif entry.get('Status') == 'ACTIVE':
                        break
                
                if not is_idle:
                    # NAT Gateway is active
                    print(f"   NAT Gateway is ACTIVE")
                    nat_stats['active'] += 1
                    
                    table.put_item(Item={
                        'ResourceId': nat_gw_id,
                        'Timestamp': int(datetime.now().timestamp()),
                        'ResourceType': 'NAT_GATEWAY',
                        'Status': 'ACTIVE',
                        'BytesOut': str(bytes_out),
                        'BytesIn': str(bytes_in),
                        'LastChecked': datetime.now().isoformat(),
                        'VpcId': nat_gw.get('VpcId'),
                        'SubnetId': nat_gw.get('SubnetId')
                    })
                
                elif idle_count < 3:
                    # First or second idle detection
                    print(f"   NAT Gateway IDLE (detection #{idle_count + 1}/3)")
                    nat_stats['idle'] += 1
                    
                    # Backup configuration
                    backup_success = backup_nat_gateway_config(nat_gw)
                    
                    # Log to DynamoDB
                    table.put_item(Item={
                        'ResourceId': nat_gw_id,
                        'Timestamp': int(datetime.now().timestamp()),
                        'ResourceType': 'NAT_GATEWAY',
                        'Status': 'IDLE_WARNING',
                        'BytesOut': str(bytes_out),
                        'BytesIn': str(bytes_in),
                        'LastChecked': datetime.now().isoformat(),
                        'IdleCount': idle_count + 1,
                        'VpcId': nat_gw.get('VpcId'),
                        'SubnetId': nat_gw.get('SubnetId'),
                        'ConfigBackupPath': f's3://{S3_BUCKET}/nat-gateway-configs/{nat_gw_id}/'
                    })
                    
                    # Send warning email on first detection
                    if idle_count == 0:
                        send_nat_gateway_alert(nat_gw, 'IDLE_WARNING', bytes_out, bytes_in)
                
                else:
                    # Idle for 3+ checks - time to delete
                    print(f"   NAT Gateway idle for {idle_count + 1} checks - DELETING")
                    nat_stats['idle'] += 1
                    
                    # Final backup
                    backup_success = backup_nat_gateway_config(nat_gw)
                    
                    # Delete NAT Gateway
                    delete_success, eip_released = delete_nat_gateway(nat_gw_id, release_eip=True)
                    
                    if delete_success:
                        nat_stats['deleted'] += 1
                        
                        # Log deletion
                        table.put_item(Item={
                            'ResourceId': nat_gw_id,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'NAT_GATEWAY',
                            'Status': 'DELETED',
                            'LastChecked': datetime.now().isoformat(),
                            'DeletedDate': datetime.now().isoformat(),
                            'ConfigBackupPath': f's3://{S3_BUCKET}/nat-gateway-configs/{nat_gw_id}/',
                            'ElasticIpReleased': eip_released,
                            'EstimatedMonthlySavings': str(32.40 + (3.60 if eip_released else 0)),  #   STRING
                            'VpcId': nat_gw.get('VpcId'),
                            'SubnetId': nat_gw.get('SubnetId')
                        })
                        
                        # Send deletion email
                        send_nat_gateway_alert(nat_gw, 'DELETED', bytes_out, bytes_in)
                    else:
                        nat_stats['errors'] += 1
            
            except Exception as e:
                print(f"   Error processing NAT Gateway {nat_gw_id}: {str(e)}")
                nat_stats['errors'] += 1
                
         
        # STEP 4: CHECK ELASTIC IPS
         
        print(f"\n" + "="*50)
        print(f"  ELASTIC IP MONITORING")
        print("="*50)
        
        # Get all Elastic IPs
        elastic_ips = get_all_elastic_ips()
        
        eip_stats = {
            'total': len(elastic_ips),
            'attached': 0,
            'unattached': 0,
            'released': 0,
            'errors': 0
        }
        
        for eip in elastic_ips:
            allocation_id = eip.get('AllocationId')
            public_ip = eip.get('PublicIp')
            
            print(f"\n Checking Elastic IP: {public_ip} ({allocation_id})")
            
            # Check for protection tag
            tags = {tag['Key']: tag['Value'] for tag in eip.get('Tags', [])}
            if tags.get('CostGuardian') == 'Ignore':
                print(f"    Skipping (protected with CostGuardian=Ignore tag)")
                continue
            
            try:
                # Check if attached
                is_unattached, association_details = is_elastic_ip_unattached(eip)
                
                if not is_unattached:
                    # EIP is attached to something
                    print(f"   Elastic IP is ATTACHED")
                    if association_details.get('InstanceId'):
                        print(f"      Instance: {association_details.get('InstanceId')}")
                    if association_details.get('NetworkInterfaceId'):
                        print(f"      Network Interface: {association_details.get('NetworkInterfaceId')}")
                    
                    eip_stats['attached'] += 1
                    
                    # Log as attached
                    table.put_item(Item={
                        'ResourceId': allocation_id,
                        'Timestamp': int(datetime.now().timestamp()),
                        'ResourceType': 'ELASTIC_IP',
                        'Status': 'ATTACHED',
                        'PublicIp': public_ip,
                        'LastChecked': datetime.now().isoformat(),
                        'AttachedTo': str(association_details)
                    })
                
                else:
                    # EIP is unattached
                    print(f"    Elastic IP is UNATTACHED (costing $3.60/month)")
                    eip_stats['unattached'] += 1
                    
                    # Get history
                    history = get_resource_history(table, allocation_id, days=30)
                    
                    # Count consecutive unattached checks
                    unattached_count = 0
                    for entry in reversed(history):
                        if entry.get('Status') == 'UNATTACHED':
                            unattached_count += 1
                        elif entry.get('Status') == 'ATTACHED':
                            break
                    
                    if unattached_count < 3:
                        # First, second, or third detection
                        print(f"    Unattached detection #{unattached_count + 1}/3")
                        
                        # Log to DynamoDB
                        table.put_item(Item={
                            'ResourceId': allocation_id,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'ELASTIC_IP',
                            'Status': 'UNATTACHED',
                            'PublicIp': public_ip,
                            'LastChecked': datetime.now().isoformat(),
                            'UnattachedCount': unattached_count + 1,
                            'MonthlyCost': '3.60'
                        })
                        
                        # Send warning email on first detection
                        if unattached_count == 0:
                            send_elastic_ip_alert(eip, 'IDLE_WARNING')
                    
                    else:
                        # Unattached for 3+ checks - release it
                        print(f"    Unattached for {unattached_count + 1} checks - RELEASING")
                        
                        release_success = release_elastic_ip(allocation_id, public_ip)
                        
                        if release_success:
                            eip_stats['released'] += 1
                            
                            # Log release
                            table.put_item(Item={
                                'ResourceId': allocation_id,
                                'Timestamp': int(datetime.now().timestamp()),
                                'ResourceType': 'ELASTIC_IP',
                                'Status': 'RELEASED',
                                'PublicIp': public_ip,
                                'LastChecked': datetime.now().isoformat(),
                                'ReleasedDate': datetime.now().isoformat(),
                                'EstimatedMonthlySavings': 3.60
                            })
                            
                            # Send release email
                            send_elastic_ip_alert(eip, 'RELEASED')
                        else:
                            eip_stats['errors'] += 1
            
            except Exception as e:
                print(f"   Error processing Elastic IP {allocation_id}: {str(e)}")
                eip_stats['errors'] += 1
        
         
        # STEP 5: CHECK RDS INSTANCES
         
        print(f"\n" + "="*50)
        print(f"  RDS DATABASE MONITORING")
        print("="*50)
        
        # Get all RDS instances
        rds_instances, rds_client = get_all_rds_instances()
        
        rds_stats = {
            'total': len(rds_instances),
            'active': 0,
            'idle': 0,
            'stopped': 0,
            'deleted': 0,
            'errors': 0
        }
        
        if rds_client is None:
            print(" Could not initialize RDS client")
        else:
            for db_instance in rds_instances:
                db_instance_identifier = db_instance['DBInstanceIdentifier']
                db_status = db_instance['DBInstanceStatus']
                
                print(f"\n Checking RDS Instance: {db_instance_identifier}")
                print(f"   Status: {db_status}")
                print(f"   Class: {db_instance.get('DBInstanceClass')}")
                print(f"    Engine: {db_instance.get('Engine')} {db_instance.get('EngineVersion')}")
                
                # Skip if not available (creating, deleting, etc.)
                if db_status not in ['available', 'stopped']:
                    print(f"    Skipping (status: {db_status})")
                    continue
                
                # Check for protection tag
                tags = {tag['Key']: tag['Value'] for tag in db_instance.get('TagList', [])}
                if tags.get('CostGuardian') == 'Ignore':
                    print(f"    Skipping (protected with CostGuardian=Ignore tag)")
                    continue
                
                try:
                    # Get history from DynamoDB
                    history = get_resource_history(table, db_instance_identifier, days=30)
                    
                    # Check usage metrics (only if available)
                    if db_status == 'available':
                        avg_connections, avg_cpu, total_iops, is_idle = check_rds_usage(db_instance_identifier)
                    else:
                        # Database is stopped - treat as idle
                        avg_connections = 0
                        avg_cpu = 0
                        total_iops = 0
                        is_idle = True
                        print(f"   Database is stopped, treating as idle")
                    
                    # Count consecutive idle checks
                    idle_count = 0
                    for entry in reversed(history):
                        if entry.get('Status') in ['IDLE', 'IDLE_WARNING']:
                            idle_count += 1
                        elif entry.get('Status') == 'ACTIVE':
                            break
                    
                    if not is_idle:
                        # RDS instance is active
                        print(f"   RDS Instance is ACTIVE")
                        rds_stats['active'] += 1
                        
                        table.put_item(Item={
                            'ResourceId': db_instance_identifier,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'RDS',
                            'Status': 'ACTIVE',
                            'AvgConnections': str(avg_connections),
                            'AvgCPU': str(avg_cpu),
                            'TotalIOPS': str(total_iops),
                            'LastChecked': datetime.now().isoformat(),
                            'DBInstanceClass': db_instance.get('DBInstanceClass'),
                            'Engine': db_instance.get('Engine')
                        })
                    
                    elif idle_count < 3:
                        # First, second, or third idle detection
                        print(f"    RDS Instance IDLE (detection #{idle_count + 1}/3)")
                        rds_stats['idle'] += 1
                        
                        # Backup configuration
                        backup_success = backup_rds_config(db_instance, rds_client)
                        
                        # Log to DynamoDB
                        table.put_item(Item={
                            'ResourceId': db_instance_identifier,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'RDS',
                            'Status': 'IDLE_WARNING',
                            'AvgConnections': str(avg_connections),
                            'AvgCPU': str(avg_cpu),
                            'TotalIOPS': str(total_iops),
                            'LastChecked': datetime.now().isoformat(),
                            'IdleCount': idle_count + 1,
                            'ConfigBackupPath': f's3://{S3_BUCKET}/rds-configs/{db_instance_identifier}/',
                            'DBInstanceClass': db_instance.get('DBInstanceClass'),
                            'Engine': db_instance.get('Engine'),
                            'MonthlyCost': str(get_rds_cost(db_instance.get('DBInstanceClass'), db_instance.get('Engine')))
                        })
                        
                        # Send warning email on first detection
                        if idle_count == 0:
                            send_rds_alert(db_instance, 'IDLE_WARNING', avg_connections, avg_cpu, total_iops)
                    
                    else:
                        # Idle for 3+ checks - time to stop/delete
                        print(f"   RDS Instance idle for {idle_count + 1} checks")
                        rds_stats['idle'] += 1
                        
                        # Final backup
                        backup_success = backup_rds_config(db_instance, rds_client)
                        
                        # Create final snapshot
                        snapshot_id = create_rds_snapshot(db_instance_identifier, rds_client)
                        
                        if snapshot_id:
                            # Decision: Stop or Delete?
                            # For first action (4th check), STOP the database
                            # For subsequent checks after stopped for 7 days, DELETE
                            
                            if db_status == 'available':
                                # Database is running - STOP it
                                print(f"   Database is RUNNING - initiating STOP")
                                
                                stop_success = stop_rds_instance(db_instance_identifier, rds_client)
                                
                                if stop_success:
                                    rds_stats['stopped'] += 1
                                    
                                    # Log stop action
                                    table.put_item(Item={
                                        'ResourceId': db_instance_identifier,
                                        'Timestamp': int(datetime.now().timestamp()),
                                        'ResourceType': 'RDS',
                                        'Status': 'STOPPED',
                                        'LastChecked': datetime.now().isoformat(),
                                        'ConfigBackupPath': f's3://{S3_BUCKET}/rds-configs/{db_instance_identifier}/',
                                        'SnapshotId': snapshot_id,
                                        'StoppedDate': datetime.now().isoformat(),
                                        'DBInstanceClass': db_instance.get('DBInstanceClass'),
                                        'Engine': db_instance.get('Engine')
                                    })
                                    
                                    # Send stopped email
                                    send_rds_alert(db_instance, 'STOPPED', avg_connections, avg_cpu, total_iops, snapshot_id)
                                else:
                                    rds_stats['errors'] += 1
                            
                            elif db_status == 'stopped':
                                # Database already stopped - check how long
                                print(f"    Database already stopped")
                                
                                # Find when it was stopped
                                stopped_date = None
                                for entry in reversed(history):
                                    if entry.get('Status') == 'STOPPED':
                                        stopped_date = entry.get('Timestamp')
                                        break
                                
                                if stopped_date:
                                    days_stopped = (datetime.now().timestamp() - float(stopped_date)) / 86400
                                    print(f"   Stopped for {days_stopped:.1f} days")
                                    
                                    if days_stopped >= 7:
                                        # Stopped for 7+ days - DELETE
                                        print(f"    Grace period expired - DELETING")
                                        
                                        delete_success = delete_rds_instance(db_instance_identifier, snapshot_id, rds_client)
                                        
                                        if delete_success:
                                            rds_stats['deleted'] += 1
                                            
                                            # Log deletion
                                            estimated_savings = get_rds_cost(
                                                db_instance.get('DBInstanceClass'),
                                                db_instance.get('Engine')
                                            )
                                            
                                            table.put_item(Item={
                                                'ResourceId': db_instance_identifier,
                                                'Timestamp': int(datetime.now().timestamp()),
                                                'ResourceType': 'RDS',
                                                'Status': 'DELETED',
                                                'LastChecked': datetime.now().isoformat(),
                                                'DeletedDate': datetime.now().isoformat(),
                                                'ConfigBackupPath': f's3://{S3_BUCKET}/rds-configs/{db_instance_identifier}/',
                                                'SnapshotId': snapshot_id,
                                                'EstimatedMonthlySavings': str(estimated_savings),
                                                'DBInstanceClass': db_instance.get('DBInstanceClass'),
                                                'Engine': db_instance.get('Engine')
                                            })
                                            
                                            # Send deletion email
                                            send_rds_alert(db_instance, 'DELETED', avg_connections, avg_cpu, total_iops, snapshot_id)
                                        else:
                                            rds_stats['errors'] += 1
                                    else:
                                        # Still in grace period
                                        print(f"    Grace period: {7 - days_stopped:.1f} days remaining")
                                        
                                        # Just log the check
                                        table.put_item(Item={
                                            'ResourceId': db_instance_identifier,
                                            'Timestamp': int(datetime.now().timestamp()),
                                            'ResourceType': 'RDS',
                                            'Status': 'STOPPED',
                                            'LastChecked': datetime.now().isoformat(),
                                            'StoppedDate': stopped_date,
                                            'DaysStopped': str(days_stopped)
                                        })
                                else:
                                    print(f"    Could not find stopped date in history")
                        else:
                            print(f"  Snapshot creation failed - skipping stop/delete")
                            rds_stats['errors'] += 1
                
                except Exception as e:
                    print(f"   Error processing RDS instance {db_instance_identifier}: {str(e)}")
                    import traceback
                    print(f"    Traceback: {traceback.format_exc()}")
                    rds_stats['errors'] += 1
             
        # STEP 6: CHECK S3 BUCKETS
         
        print(f"\n" + "="*50)
        print(f"  S3 BUCKET MONITORING")
        print("="*50)
        
        # Get all S3 buckets
        s3_buckets = get_all_s3_buckets()
        
        s3_stats = {
            'total': len(s3_buckets),
            'empty': 0,
            'has_data': 0,
            'protected': 0,
            'lifecycle_applied': 0,
            'deleted': 0,
            'errors': 0
        }
        
        for bucket in s3_buckets:
            bucket_name = bucket['Name']
            
            print(f"\n Checking S3 Bucket: {bucket_name}")
            
            try:
                # Get bucket tags
                tags = get_bucket_tags(bucket_name)
                
                # Check for protection tags
                if tags.get('CostGuardianBucket') == 'Protected' or tags.get('CostGuardianBucket') == 'Prime':
                    print(f"    Skipping (protected bucket)")
                    s3_stats['protected'] += 1
                    continue
                
                # Check if bucket is empty
                is_empty, object_count, size_mb = is_bucket_empty(bucket_name)
                
                if is_empty:
                    print(f"   Bucket is EMPTY")
                    s3_stats['empty'] += 1
                    
                    # Get history from DynamoDB
                    history = get_resource_history(table, bucket_name, days=30)
                    
                    # Count consecutive empty checks
                    empty_count = 0
                    for entry in reversed(history):
                        if entry.get('Status') == 'EMPTY':
                            empty_count += 1
                        elif entry.get('Status') in ['HAS_DATA', 'ACTIVE']:
                            break
                    
                    if empty_count < 3:
                        # First, second, or third empty detection
                        print(f"    Empty detection #{empty_count + 1}/3")
                        
                        # Log to DynamoDB
                        table.put_item(Item={
                            'ResourceId': bucket_name,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'S3_BUCKET',
                            'Status': 'EMPTY',
                            'ObjectCount': 0,
                            'SizeMB': '0',
                            'LastChecked': datetime.now().isoformat(),
                            'EmptyCount': empty_count + 1
                        })
                        
                        # Send warning email on first detection
                        if empty_count == 0:
                            send_s3_bucket_alert(bucket_name, 'EMPTY_WARNING')
                    
                    else:
                        # Empty for 3+ checks - delete it
                        print(f"    Empty for {empty_count + 1} checks - DELETING")
                        
                        delete_success = delete_empty_bucket(bucket_name)
                        
                        if delete_success:
                            s3_stats['deleted'] += 1
                            
                            # Log deletion
                            table.put_item(Item={
                                'ResourceId': bucket_name,
                                'Timestamp': int(datetime.now().timestamp()),
                                'ResourceType': 'S3_BUCKET',
                                'Status': 'DELETED',
                                'LastChecked': datetime.now().isoformat(),
                                'DeletedDate': datetime.now().isoformat(),
                                'Reason': 'Empty for 3+ consecutive checks'
                            })
                            
                            # Send deletion email
                            send_s3_bucket_alert(bucket_name, 'DELETED')
                        else:
                            s3_stats['errors'] += 1
                
                else:
                    # Bucket has data
                    print(f"   Bucket has data ({object_count:,} objects, {size_mb:.2f} MB)")
                    s3_stats['has_data'] += 1
                    
                    # Check if lifecycle policy exists
                    try:
                        s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
                        print(f"    Lifecycle policy already exists")
                    
                    except s3_client.exceptions.ClientError as e:
                        error_code = e.response['Error']['Code']
                        
                        if error_code == 'NoSuchLifecycleConfiguration':
                            # No lifecycle policy - apply one
                            print(f"   No lifecycle policy found - applying 3-tier optimization")
                            
                            lifecycle_success = apply_lifecycle_policy(bucket_name)
                            
                            if lifecycle_success:
                                s3_stats['lifecycle_applied'] += 1
                                
                                # Log lifecycle application
                                table.put_item(Item={
                                    'ResourceId': bucket_name,
                                    'Timestamp': int(datetime.now().timestamp()),
                                    'ResourceType': 'S3_BUCKET',
                                    'Status': 'LIFECYCLE_APPLIED',
                                    'ObjectCount': object_count,
                                    'SizeMB': str(size_mb),
                                    'LastChecked': datetime.now().isoformat(),
                                    'LifecyclePolicy': '3-Tier (Standard→IA→Glacier)'
                                })
                                
                                # Send notification
                                send_s3_bucket_alert(bucket_name, 'LIFECYCLE_APPLIED', object_count, size_mb)
                            else:
                                s3_stats['errors'] += 1
                    
                    # Log as active bucket with data
                    table.put_item(Item={
                        'ResourceId': bucket_name,
                        'Timestamp': int(datetime.now().timestamp()),
                        'ResourceType': 'S3_BUCKET',
                        'Status': 'HAS_DATA',
                        'ObjectCount': object_count,
                        'SizeMB': str(size_mb),
                        'LastChecked': datetime.now().isoformat()
                    })
            
            except Exception as e:
                print(f"   Error processing bucket {bucket_name}: {str(e)}")
                s3_stats['errors'] += 1
        
         
        # STEP 7: CHECK EBS VOLUMES
         
        print(f"\n" + "="*50)
        print(f"  EBS VOLUME MONITORING")
        print("="*50)
        
        # Get all EBS volumes
        ebs_volumes = get_all_ebs_volumes()
        
        ebs_stats = {
            'total': len(ebs_volumes),
            'in_use': 0,
            'available': 0,
            'snapshots_created': 0,
            'deleted': 0,
            'errors': 0
        }
        
        for volume in ebs_volumes:
            volume_id = volume['VolumeId']
            volume_state = volume.get('State')
            size_gb = volume.get('Size')
            volume_type = volume.get('VolumeType')
            
            # Get volume name from tags
            tags = {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}
            volume_name = tags.get('Name', 'Unnamed')
            
            print(f"\n Checking EBS Volume: {volume_id}")
            print(f"  Name: {volume_name}")
            print(f"   Size: {size_gb} GB ({volume_type})")
            print(f"   State: {volume_state}")
            
            # Skip volumes that are being created/deleted
            if volume_state in ['creating', 'deleting']:
                print(f"     Skipping (status: {volume_state})")
                continue
            
            # Check for protection tag
            if tags.get('CostGuardian') == 'Ignore':
                print(f"     Skipping (protected with CostGuardian=Ignore tag)")
                continue
            
            try:
                if volume_state == 'in-use':
                    # Volume is attached to an instance
                    attachments = volume.get('Attachments', [])
                    if attachments:
                        instance_id = attachments[0].get('InstanceId')
                        device = attachments[0].get('Device')
                        print(f"    Volume is IN-USE (attached to {instance_id} as {device})")
                    else:
                        print(f"    Volume is IN-USE")
                    
                    ebs_stats['in_use'] += 1
                    
                    # Log as in-use
                    table.put_item(Item={
                        'ResourceId': volume_id,
                        'Timestamp': int(datetime.now().timestamp()),
                        'ResourceType': 'EBS_VOLUME',
                        'Status': 'IN_USE',
                        'SizeGB': size_gb,
                        'VolumeType': volume_type,
                        'LastChecked': datetime.now().isoformat(),
                        'VolumeName': volume_name
                    })
                
                elif volume_state == 'available':
                    # Volume is unattached (available for attachment)
                    estimated_cost = get_ebs_volume_cost(volume_type, size_gb, volume.get('Iops'))
                    
                    print(f"     Volume is AVAILABLE (unattached)")
                    print(f"    Monthly waste: ${estimated_cost:.2f}")
                    
                    ebs_stats['available'] += 1
                    
                    # Get history from DynamoDB
                    history = get_resource_history(table, volume_id, days=30)
                    
                    # Count consecutive available (unattached) checks
                    available_count = 0
                    for entry in reversed(history):
                        if entry.get('Status') == 'AVAILABLE':
                            available_count += 1
                        elif entry.get('Status') == 'IN_USE':
                            break
                    
                    if available_count < 3:
                        # First, second, or third detection
                        print(f"     Unattached detection #{available_count + 1}/3")
                        
                        # Backup configuration
                        backup_success = backup_ebs_volume_config(volume)
                        
                        # Log to DynamoDB
                        table.put_item(Item={
                            'ResourceId': volume_id,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'EBS_VOLUME',
                            'Status': 'AVAILABLE',
                            'SizeGB': size_gb,
                            'VolumeType': volume_type,
                            'LastChecked': datetime.now().isoformat(),
                            'UnattachedCount': available_count + 1,
                            'ConfigBackupPath': f's3://{S3_BUCKET}/ebs-volumes/{volume_id}/',
                            'MonthlyCost': str(estimated_cost),
                            'VolumeName': volume_name
                        })
                        
                        # Send warning email on first detection
                        if available_count == 0:
                            send_ebs_volume_alert(volume, 'UNATTACHED_WARNING')
                    
                    else:
                        # Unattached for 3+ checks - create snapshot and delete
                        print(f"    Unattached for {available_count + 1} checks - DELETING")
                        
                        # Final backup
                        backup_success = backup_ebs_volume_config(volume)
                        
                        # Create snapshot
                        snapshot_id = create_ebs_snapshot(volume_id, volume_name)
                        
                        if snapshot_id:
                            ebs_stats['snapshots_created'] += 1
                            
                            # Wait a moment for snapshot to initialize
                            import time
                            print(f"    Waiting 5 seconds for snapshot to initialize...")
                            time.sleep(5)
                            
                            # Delete volume
                            delete_success = delete_ebs_volume(volume_id)
                            
                            if delete_success:
                                ebs_stats['deleted'] += 1
                                
                                # Calculate savings (volume cost minus snapshot cost)
                                snapshot_cost = size_gb * 0.05  # $0.05/GB/month for snapshots
                                net_savings = estimated_cost - snapshot_cost
                                
                                # Log deletion
                                table.put_item(Item={
                                    'ResourceId': volume_id,
                                    'Timestamp': int(datetime.now().timestamp()),
                                    'ResourceType': 'EBS_VOLUME',
                                    'Status': 'DELETED',
                                    'LastChecked': datetime.now().isoformat(),
                                    'DeletedDate': datetime.now().isoformat(),
                                    'ConfigBackupPath': f's3://{S3_BUCKET}/ebs-volumes/{volume_id}/',
                                    'SnapshotId': snapshot_id,
                                    'SizeGB': size_gb,
                                    'VolumeType': volume_type,
                                    'EstimatedMonthlySavings': str(net_savings),
                                    'SnapshotCost': str(snapshot_cost),
                                    'VolumeName': volume_name
                                })
                                
                                # Send deletion email
                                send_ebs_volume_alert(volume, 'DELETED', snapshot_id)
                                
                                print(f"    Volume deleted successfully")
                                print(f"    Net monthly savings: ${net_savings:.2f}")
                                print(f"    Snapshot preserved: {snapshot_id} (costs ${snapshot_cost:.2f}/month)")
                            else:
                                ebs_stats['errors'] += 1
                        else:
                            print(f"    Snapshot creation failed - skipping deletion")
                            ebs_stats['errors'] += 1
            
            except Exception as e:
                print(f"    Error processing volume {volume_id}: {str(e)}")
                import traceback
                print(f"    Traceback: {traceback.format_exc()}")
                ebs_stats['errors'] += 1
        
         
        # STEP 8: CHECK LOAD BALANCERS (ALB/NLB)
         
        if ENABLE_LB_MONITORING:
            print(f"\n" + "="*50)
            print(f"⚖️  LOAD BALANCER MONITORING (ALB/NLB)")
            print("="*50)
            
            # Get all load balancers
            load_balancers = get_all_load_balancers()
            
            lb_stats = {
                'total': len(load_balancers),
                'active': 0,
                'idle': 0,
                'deleted': 0,
                'errors': 0
            }
            
            for lb in load_balancers:
                lb_arn = lb['LoadBalancerArn']
                lb_name = lb['LoadBalancerName']
                lb_type = lb.get('Type', 'application')
                
                print(f"\n  Checking Load Balancer: {lb_name}")
                print(f"    Type: {lb_type.upper()}")
                print(f"    DNS: {lb.get('DNSName')}")
                
                # Check for protection tag
                try:
                    tags_response = elbv2_client.describe_tags(ResourceArns=[lb_arn])
                    tags = {}
                    if tags_response.get('TagDescriptions'):
                        tags = {tag['Key']: tag['Value'] for tag in tags_response['TagDescriptions'][0].get('Tags', [])}
                    
                    if tags.get('CostGuardian') == 'Ignore':
                        print(f"     Skipping (protected with CostGuardian=Ignore tag)")
                        continue
                except Exception as e:
                    print(f"     Could not fetch tags: {str(e)}")
                    tags = {}
                
                try:
                    # Check usage metrics
                    healthy_targets, connections, bytes_processed, is_idle = check_load_balancer_usage(
                        lb_arn, lb_name, lb_type
                    )
                    
                    # Get history from DynamoDB
                    history = get_resource_history(table, lb_arn, days=30)
                    
                    # Count consecutive idle checks
                    idle_count = 0
                    for entry in reversed(history):
                        if entry.get('Status') in ['IDLE', 'IDLE_WARNING']:
                            idle_count += 1
                        elif entry.get('Status') == 'ACTIVE':
                            break
                    
                    if not is_idle:
                        # Load balancer is active
                        print(f"    Load Balancer is ACTIVE")
                        lb_stats['active'] += 1
                        
                        table.put_item(Item={
                            'ResourceId': lb_arn,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'LOAD_BALANCER',
                            'Status': 'ACTIVE',
                            'LoadBalancerName': lb_name,
                            'LoadBalancerType': lb_type,
                            'HealthyTargets': str(healthy_targets),
                            'Connections': str(connections),
                            'BytesProcessed': str(bytes_processed),
                            'LastChecked': datetime.now().isoformat()
                        })
                    
                    elif idle_count < LB_IDLE_CHECKS:
                        # First, second, or third idle detection
                        print(f"     Load Balancer IDLE (detection #{idle_count + 1}/{LB_IDLE_CHECKS})")
                        lb_stats['idle'] += 1
                        
                        # Backup configuration
                        backup_success = backup_load_balancer_config(lb, lb_type)
                        
                        # Log to DynamoDB
                        table.put_item(Item={
                            'ResourceId': lb_arn,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'LOAD_BALANCER',
                            'Status': 'IDLE_WARNING',
                            'LoadBalancerName': lb_name,
                            'LoadBalancerType': lb_type,
                            'HealthyTargets': str(healthy_targets),
                            'Connections': str(connections),
                            'BytesProcessed': str(bytes_processed),
                            'LastChecked': datetime.now().isoformat(),
                            'IdleCount': idle_count + 1,
                            'ConfigBackupPath': f's3://{S3_BUCKET}/load-balancers/{lb_name}/',
                            'MonthlyCost': str(16.20 if lb_type in ['application', 'network'] else 18.00)
                        })
                        
                        # Send warning email on first detection
                        if idle_count == 0:
                            send_load_balancer_alert(lb, 'IDLE_WARNING', healthy_targets, connections, bytes_processed)
                    
                    else:
                        # Idle for 3+ checks - delete
                        print(f"    Load Balancer idle for {idle_count + 1} checks - DELETING")
                        lb_stats['idle'] += 1
                        
                        # Final backup
                        backup_success = backup_load_balancer_config(lb, lb_type)
                        
                        # Delete load balancer
                        delete_success = delete_load_balancer(lb_arn, lb_name)
                        
                        if delete_success:
                            lb_stats['deleted'] += 1
                            
                            monthly_savings = 16.20 if lb_type in ['application', 'network'] else 18.00
                            
                            # Log deletion
                            table.put_item(Item={
                                'ResourceId': lb_arn,
                                'Timestamp': int(datetime.now().timestamp()),
                                'ResourceType': 'LOAD_BALANCER',
                                'Status': 'DELETED',
                                'LoadBalancerName': lb_name,
                                'LoadBalancerType': lb_type,
                                'LastChecked': datetime.now().isoformat(),
                                'DeletedDate': datetime.now().isoformat(),
                                'ConfigBackupPath': f's3://{S3_BUCKET}/load-balancers/{lb_name}/',
                                'EstimatedMonthlySavings': str(monthly_savings)
                            })
                            
                            # Send deletion email
                            send_load_balancer_alert(lb, 'DELETED', healthy_targets, connections, bytes_processed)
                        else:
                            lb_stats['errors'] += 1
                
                except Exception as e:
                    print(f"    Error processing load balancer {lb_name}: {str(e)}")
                    import traceback
                    print(f"    Traceback: {traceback.format_exc()}")
                    lb_stats['errors'] += 1
         
        # STEP 9: CHECK ORPHANED VPCS
         
        if ENABLE_VPC_CLEANUP:
            print(f"\n" + "="*50)
            print(f"🌐 VPC CLEANUP MONITORING")
            print("="*50)
            
            # Get all VPCs
            vpcs = get_all_vpcs()
            
            vpc_stats = {
                'total': len(vpcs),
                'empty': 0,
                'active': 0,
                'deleted': 0,
                'errors': 0
            }
            
            for vpc in vpcs:
                vpc_id = vpc['VpcId']
                
                # Get VPC name
                vpc_name = "Unnamed"
                for tag in vpc.get('Tags', []):
                    if tag['Key'] == 'Name':
                        vpc_name = tag['Value']
                        break
                
                print(f"\n  Checking VPC: {vpc_name} ({vpc_id})")
                print(f"    CIDR: {vpc.get('CidrBlock')}")
                print(f"    Default: {vpc.get('IsDefault')}")
                
                # Skip default VPC
                if vpc.get('IsDefault'):
                    print(f"     Skipping (default VPC - protected)")
                    vpc_stats['active'] += 1
                    continue
                
                # Check for protection tag
                tags = {tag['Key']: tag['Value'] for tag in vpc.get('Tags', [])}
                if tags.get('CostGuardian') == 'Ignore':
                    print(f"     Skipping (protected with CostGuardian=Ignore tag)")
                    vpc_stats['active'] += 1
                    continue
                
                try:
                    # Check if VPC is empty
                    is_empty, resource_count, resource_summary = is_vpc_empty(vpc_id)
                    
                    # Get history from DynamoDB
                    history = get_resource_history(table, vpc_id, days=30)
                    
                    # Count consecutive empty checks
                    empty_count = 0
                    for entry in reversed(history):
                        if entry.get('Status') in ['EMPTY', 'EMPTY_WARNING']:
                            empty_count += 1
                        elif entry.get('Status') == 'ACTIVE':
                            break
                    
                    if not is_empty:
                        # VPC has resources
                        print(f"    VPC is ACTIVE ({resource_count} resources)")
                        vpc_stats['active'] += 1
                        
                        table.put_item(Item={
                            'ResourceId': vpc_id,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'VPC',
                            'Status': 'ACTIVE',
                            'VpcName': vpc_name,
                            'CidrBlock': vpc.get('CidrBlock'),
                            'ResourceCount': resource_count,
                            'LastChecked': datetime.now().isoformat()
                        })
                    
                    elif empty_count < VPC_IDLE_CHECKS:
                        # First, second, or third empty detection
                        print(f"     VPC is EMPTY (detection #{empty_count + 1}/{VPC_IDLE_CHECKS})")
                        vpc_stats['empty'] += 1
                        
                        # Backup configuration
                        backup_success = backup_vpc_config(vpc)
                        
                        # Log to DynamoDB
                        table.put_item(Item={
                            'ResourceId': vpc_id,
                            'Timestamp': int(datetime.now().timestamp()),
                            'ResourceType': 'VPC',
                            'Status': 'EMPTY_WARNING',
                            'VpcName': vpc_name,
                            'CidrBlock': vpc.get('CidrBlock'),
                            'ResourceCount': resource_count,
                            'EmptyCount': empty_count + 1,
                            'LastChecked': datetime.now().isoformat(),
                            'ConfigBackupPath': f's3://{S3_BUCKET}/vpc-configs/{vpc_id}/'
                        })
                        
                        # Send warning email on first detection
                        if empty_count == 0:
                            send_vpc_alert(vpc, 'IDLE_WARNING', resource_count, resource_summary)
                    
                    else:
                        # Empty for 3+ checks - delete
                        print(f"    VPC empty for {empty_count + 1} checks - DELETING")
                        vpc_stats['empty'] += 1
                        
                        # Final backup
                        backup_success = backup_vpc_config(vpc)
                        
                        # Delete VPC (this handles all dependencies)
                        delete_success = delete_vpc(vpc_id)
                        
                        if delete_success:
                            vpc_stats['deleted'] += 1
                            
                            # Log deletion
                            table.put_item(Item={
                                'ResourceId': vpc_id,
                                'Timestamp': int(datetime.now().timestamp()),
                                'ResourceType': 'VPC',
                                'Status': 'DELETED',
                                'VpcName': vpc_name,
                                'CidrBlock': vpc.get('CidrBlock'),
                                'LastChecked': datetime.now().isoformat(),
                                'DeletedDate': datetime.now().isoformat(),
                                'ConfigBackupPath': f's3://{S3_BUCKET}/vpc-configs/{vpc_id}/'
                            })
                            
                            # Send deletion email
                            send_vpc_alert(vpc, 'DELETED', resource_count, resource_summary)
                        else:
                            vpc_stats['errors'] += 1
                
                except Exception as e:
                    print(f"    Error processing VPC {vpc_id}: {str(e)}")
                    import traceback
                    print(f"    Traceback: {traceback.format_exc()}")
                    vpc_stats['errors'] += 1
            
            # Print summary
            print(f"\n" + "="*50)
            print(f"  VPC Cleanup Summary:")
            print(f"  Total VPCs: {vpc_stats['total']}")
            print(f"  Active (with resources): {vpc_stats['active']}")
            print(f"  Empty: {vpc_stats['empty']}")
            print(f"  Deleted: {vpc_stats['deleted']}")
            print(f"  Errors: {vpc_stats['errors']}")
            if vpc_stats['deleted'] > 0:
                print(f"  🧹 Cleanup completed")
            print("="*50)
            
        # Print summary
        print(f"\n" + "="*50)
        print(f"  Load Balancer Summary:")
        print(f"  Total Load Balancers: {lb_stats['total']}")
        print(f"  Active: {lb_stats['active']}")
        print(f"  Idle: {lb_stats['idle']}")
        print(f"  Deleted: {lb_stats['deleted']}")
        print(f"  Errors: {lb_stats['errors']}")
        if lb_stats['deleted'] > 0:
            savings = lb_stats['deleted'] * 16.20
            print(f"    Monthly Savings: ${savings:.2f}")
        print("="*50)
        
        # Print EBS summary
        print(f"\n" + "="*50)
        print(f"  EBS Volume Summary:")
        print(f"  Total Volumes: {ebs_stats['total']}")
        print(f"  In Use (attached): {ebs_stats['in_use']}")
        print(f"  Available (unattached): {ebs_stats['available']}")
        print(f"  Snapshots Created: {ebs_stats['snapshots_created']}")
        print(f"  Deleted: {ebs_stats['deleted']}")
        print(f"  Errors: {ebs_stats['errors']}")
        if ebs_stats['deleted'] > 0:
            print(f"    Volume cleanup completed")
        print("="*50)
        
        # Print S3 summary
        print(f"\n" + "="*50)
        print(f"  S3 Bucket Summary:")
        print(f"  Total Buckets: {s3_stats['total']}")
        print(f"  With Data: {s3_stats['has_data']}")
        print(f"  Empty: {s3_stats['empty']}")
        print(f"  Protected: {s3_stats['protected']}")
        print(f"  Lifecycle Applied: {s3_stats['lifecycle_applied']}")
        print(f"  Deleted: {s3_stats['deleted']}")
        print(f"  Errors: {s3_stats['errors']}")
        print("="*50)
        
        # Print RDS summary
        print(f"\n" + "="*50)
        print(f"  RDS Summary:")
        print(f"  Total RDS Instances: {rds_stats['total']}")
        print(f"  Active: {rds_stats['active']}")
        print(f"  Idle: {rds_stats['idle']}")
        print(f"  Stopped: {rds_stats['stopped']}")
        print(f"  Deleted: {rds_stats['deleted']}")
        print(f"  Errors: {rds_stats['errors']}")
        if rds_stats['deleted'] > 0 or rds_stats['stopped'] > 0:
            # Calculate approximate savings
            # Stopped = ~80% savings, Deleted = 100% savings
            print(f"    Estimated savings this run")
        print("="*50)
        
        # Print Elastic IP summary
        print(f"\n" + "="*50)
        print(f"  Elastic IP Summary:")
        print(f"  Total Elastic IPs: {eip_stats['total']}")
        print(f"  Attached: {eip_stats['attached']}")
        print(f"  Unattached: {eip_stats['unattached']}")
        print(f"  Released: {eip_stats['released']}")
        print(f"  Errors: {eip_stats['errors']}")
        if eip_stats['released'] > 0:
            monthly_savings = eip_stats['released'] * 3.60
            print(f"    Monthly Savings: ${monthly_savings:.2f}")
        print("="*50)
        
        # Print NAT Gateway summary
        print(f"\n" + "="*50)
        print(f"  NAT Gateway Summary:")
        print(f"  Total NAT Gateways: {nat_stats['total']}")
        print(f"  Active: {nat_stats['active']}")
        print(f"  Idle: {nat_stats['idle']}")
        print(f"  Deleted: {nat_stats['deleted']}")
        print(f"  Errors: {nat_stats['errors']}")
        if nat_stats['deleted'] > 0:
            monthly_savings = nat_stats['deleted'] * 32.40
            print(f"    Monthly Savings: ${monthly_savings:.2f}")
        print("="*50)
        
        '''log_metric('TotalInstances', stats['total_instances'])
        log_metric('IdleInstances', stats['idle_instances'])
        log_metric('BackupsCreated', stats['backed_up'])
        log_metric('Errors', stats['errors'])'''
        
        print("\n" + "="*50)
        print("  CostGuardian Summary:")
        print(f"  Total instances: {stats['total_instances']}")
        print(f"  Active: {stats['active_instances']}")
        print(f"  Idle: {stats['idle_instances']}")
        print(f"  Backed up: {stats['backed_up']}")
        print(f"  Errors: {stats['errors']}")
        print("="*50)

         
        # SEND METRICS TO CLOUDWATCH FOR DASHBOARD
         
        print(f"\n" + "="*50)
        print(f"  Sending metrics to CloudWatch...")
        print("="*50)
        
        # Calculate total monthly savings
        total_monthly_savings = 0.0
        total_deleted = 0
        total_warned = 0
        
        # EC2 savings
        if 'stats' in locals():
            total_deleted += stats.get('backed_up', 0)
        
        # NAT Gateway savings ($32.40 each)
        if 'nat_stats' in locals():
            nat_savings = nat_stats.get('deleted', 0) * 32.40
            total_monthly_savings += nat_savings
            total_deleted += nat_stats.get('deleted', 0)
            total_warned += nat_stats.get('idle', 0)
            send_cloudwatch_metric('NATGatewayDeleted', nat_stats.get('deleted', 0))
        
        # Elastic IP savings ($3.60 each)
        if 'eip_stats' in locals():
            eip_savings = eip_stats.get('released', 0) * 3.60
            total_monthly_savings += eip_savings
            total_deleted += eip_stats.get('released', 0)
            send_cloudwatch_metric('ElasticIPReleased', eip_stats.get('released', 0))
        
        # EBS Volume savings
        if 'ebs_stats' in locals():
            # Estimate $8 per volume (average)
            ebs_savings = ebs_stats.get('deleted', 0) * 3.0  # Net savings after snapshot
            total_monthly_savings += ebs_savings
            total_deleted += ebs_stats.get('deleted', 0)
            total_warned += ebs_stats.get('available', 0)
            send_cloudwatch_metric('EBSVolumeDeleted', ebs_stats.get('deleted', 0))
        
        # RDS savings
        if 'rds_stats' in locals():
            # Estimate $50 per RDS instance (conservative)
            rds_savings = rds_stats.get('deleted', 0) * 50.0
            total_monthly_savings += rds_savings
            total_deleted += rds_stats.get('deleted', 0)
            total_warned += rds_stats.get('idle', 0)
            send_cloudwatch_metric('RDSDeleted', rds_stats.get('deleted', 0))
        
        # S3 savings (lifecycle applied)
        if 's3_stats' in locals():
            send_cloudwatch_metric('S3LifecycleApplied', s3_stats.get('lifecycle_applied', 0))
        
        # Load Balancer savings ($16.20 each)
        if 'lb_stats' in locals():
            lb_savings = lb_stats.get('deleted', 0) * 16.20
            total_monthly_savings += lb_savings
            total_deleted += lb_stats.get('deleted', 0)
            total_warned += lb_stats.get('idle', 0)
            send_cloudwatch_metric('LoadBalancerDeleted', lb_stats.get('deleted', 0))
        
        # VPC cleanup (no direct cost savings but send metrics)
        if 'vpc_stats' in locals():
            send_cloudwatch_metric('VPCDeleted', vpc_stats.get('deleted', 0))
        
        # Send aggregate metrics
        send_cloudwatch_metric('MonthlySavings', total_monthly_savings, 'None')
        send_cloudwatch_metric('ResourcesDeleted', total_deleted)
        send_cloudwatch_metric('ResourcesWarned', total_warned)
        
        print(f"  Metrics sent successfully")
        print(f"    Total Monthly Savings: ${total_monthly_savings:.2f}")
        print(f"    Total Annual Savings: ${total_monthly_savings * 12:.2f}")
        print(f"    Resources Deleted This Run: {total_deleted}")
        print(f"     Resources Warned This Run: {total_warned}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'EC2 monitoring completed',
                'stats': stats
            })
        }
        
    
    except Exception as e:
        print(f"  Fatal error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }
        
        
        


def get_cpu_utilization(instance_id):
    
    try:
        # Define time range (last 24 hours)
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=24)
        
        # Query CloudWatch Metrics API
        response = cloudwatch.get_metric_statistics(
            Namespace='AWS/EC2',              # Where EC2 metrics are stored
            MetricName='CPUUtilization',       # The metric we want
            Dimensions=[{
                'Name': 'InstanceId',
                'Value': instance_id
            }],
            StartTime=start_time,
            EndTime=end_time,
            Period=3600,                       # 1 hour intervals (3600 seconds)
            Statistics=['Average']             # Calculate average CPU
        )
        
        # Extract data points
        datapoints = response['Datapoints']
        
        # If no data, assume 0% (might be just launched)
        if not datapoints:
            print(f"     No CloudWatch data for {instance_id} (newly launched?)")
            return 0.0
        
        # Calculate overall average
        cpu_values = [dp['Average'] for dp in datapoints]
        average_cpu = sum(cpu_values) / len(cpu_values)
        
        return average_cpu
    
    except Exception as e:
        print(f"    Error getting metrics: {str(e)}")
        return 100.0  # Assume active if can't read metrics



 
# HELPER FUNCTION: BACKUP CONFIGURATION (ENHANCED)
 
def backup_instance_config(instance, instance_id):
    """
    Saves complete EC2 instance configuration to S3
    Including security groups, VPC, subnets, and network details
    """
    
    try:
        print(f"    Backing up configuration for {instance_id}...")
        
         
        # STEP 1: BASIC INSTANCE CONFIGURATION
         
        config = {
            'InstanceId': instance_id,
            'InstanceType': instance.get('InstanceType'),
            'ImageId': instance.get('ImageId'),
            'LaunchTime': str(instance.get('LaunchTime')),
            'State': instance['State']['Name'],
            'PrivateIpAddress': instance.get('PrivateIpAddress'),
            'PublicIpAddress': instance.get('PublicIpAddress'),
            'SubnetId': instance.get('SubnetId'),
            'VpcId': instance.get('VpcId'),
            'KeyName': instance.get('KeyName'),
            'Tags': instance.get('Tags', []),
            'BlockDeviceMappings': instance.get('BlockDeviceMappings', []),
            'IamInstanceProfile': instance.get('IamInstanceProfile', {}),
            'BackupTimestamp': datetime.now().isoformat()
        }
        
         
        # STEP 2: DETAILED SECURITY GROUP RULES
         
        security_groups_detailed = []
        
        for sg in instance.get('SecurityGroups', []):
            sg_id = sg['GroupId']
            
            try:
                # Fetch complete security group details
                sg_response = ec2_client.describe_security_groups(
                    GroupIds=[sg_id]
                )
                
                if sg_response['SecurityGroups']:
                    sg_details = sg_response['SecurityGroups'][0]
                    
                    # Extract and format the rules
                    security_groups_detailed.append({
                        'GroupId': sg_id,
                        'GroupName': sg_details['GroupName'],
                        'Description': sg_details['Description'],
                        'VpcId': sg_details.get('VpcId'),
                        
                        # Inbound Rules (Ingress)
                        'InboundRules': [
                            {
                                'Protocol': rule.get('IpProtocol', 'all'),
                                'FromPort': rule.get('FromPort', 'N/A'),
                                'ToPort': rule.get('ToPort', 'N/A'),
                                'IpRanges': [
                                    {
                                        'CidrIp': ip_range.get('CidrIp'),
                                        'Description': ip_range.get('Description', '')
                                    }
                                    for ip_range in rule.get('IpRanges', [])
                                ],
                                'Ipv6Ranges': [
                                    {
                                        'CidrIpv6': ip_range.get('CidrIpv6'),
                                        'Description': ip_range.get('Description', '')
                                    }
                                    for ip_range in rule.get('Ipv6Ranges', [])
                                ],
                                'UserIdGroupPairs': [
                                    {
                                        'GroupId': pair.get('GroupId'),
                                        'Description': pair.get('Description', '')
                                    }
                                    for pair in rule.get('UserIdGroupPairs', [])
                                ]
                            }
                            for rule in sg_details.get('IpPermissions', [])
                        ],
                        
                        # Outbound Rules (Egress)
                        'OutboundRules': [
                            {
                                'Protocol': rule.get('IpProtocol', 'all'),
                                'FromPort': rule.get('FromPort', 'N/A'),
                                'ToPort': rule.get('ToPort', 'N/A'),
                                'IpRanges': [
                                    {
                                        'CidrIp': ip_range.get('CidrIp'),
                                        'Description': ip_range.get('Description', '')
                                    }
                                    for ip_range in rule.get('IpRanges', [])
                                ],
                                'Ipv6Ranges': [
                                    {
                                        'CidrIpv6': ip_range.get('CidrIpv6'),
                                        'Description': ip_range.get('Description', '')
                                    }
                                    for ip_range in rule.get('Ipv6Ranges', [])
                                ],
                                'UserIdGroupPairs': [
                                    {
                                        'GroupId': pair.get('GroupId'),
                                        'Description': pair.get('Description', '')
                                    }
                                    for pair in rule.get('UserIdGroupPairs', [])
                                ]
                            }
                            for rule in sg_details.get('IpPermissionsEgress', [])
                        ],
                        
                        'Tags': sg_details.get('Tags', [])
                    })
                    
                    print(f"      Captured security group: {sg_details['GroupName']}")
            
            except Exception as e:
                print(f"       Could not fetch details for SG {sg_id}: {str(e)}")
                # Add basic info even if detailed fetch fails
                security_groups_detailed.append({
                    'GroupId': sg_id,
                    'GroupName': sg.get('GroupName', 'Unknown'),
                    'Error': str(e)
                })
        
        config['SecurityGroupsDetailed'] = security_groups_detailed
        
         
        # STEP 3: VPC DETAILS
         
        vpc_id = instance.get('VpcId')
        if vpc_id:
            try:
                vpc_response = ec2_client.describe_vpcs(VpcIds=[vpc_id])
                
                if vpc_response['Vpcs']:
                    vpc_details = vpc_response['Vpcs'][0]
                    
                    config['VpcDetails'] = {
                        'VpcId': vpc_id,
                        'CidrBlock': vpc_details.get('CidrBlock'),
                        'State': vpc_details.get('State'),
                        'DhcpOptionsId': vpc_details.get('DhcpOptionsId'),
                        'IsDefault': vpc_details.get('IsDefault'),
                        'EnableDnsHostnames': vpc_details.get('EnableDnsHostnames'),
                        'EnableDnsSupport': vpc_details.get('EnableDnsSupport'),
                        'Tags': vpc_details.get('Tags', [])
                    }
                    
                    # Get Internet Gateway attached to this VPC
                    igw_response = ec2_client.describe_internet_gateways(
                        Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
                    )
                    
                    config['VpcDetails']['InternetGateways'] = [
                        {
                            'InternetGatewayId': igw['InternetGatewayId'],
                            'State': igw['Attachments'][0]['State'] if igw.get('Attachments') else 'detached'
                        }
                        for igw in igw_response.get('InternetGateways', [])
                    ]
                    
                    print(f"      Captured VPC details: {vpc_id}")
            
            except Exception as e:
                print(f"       Could not fetch VPC details: {str(e)}")
                config['VpcDetails'] = {'VpcId': vpc_id, 'Error': str(e)}
        
         
        # STEP 4: SUBNET DETAILS
         
        subnet_id = instance.get('SubnetId')
        if subnet_id:
            try:
                subnet_response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
                
                if subnet_response['Subnets']:
                    subnet_details = subnet_response['Subnets'][0]
                    
                    config['SubnetDetails'] = {
                        'SubnetId': subnet_id,
                        'AvailabilityZone': subnet_details.get('AvailabilityZone'),
                        'AvailabilityZoneId': subnet_details.get('AvailabilityZoneId'),
                        'CidrBlock': subnet_details.get('CidrBlock'),
                        'State': subnet_details.get('State'),
                        'MapPublicIpOnLaunch': subnet_details.get('MapPublicIpOnLaunch'),
                        'DefaultForAz': subnet_details.get('DefaultForAz'),
                        'Tags': subnet_details.get('Tags', [])
                    }
                    
                    # Get Route Table for this subnet
                    rt_response = ec2_client.describe_route_tables(
                        Filters=[{'Name': 'association.subnet-id', 'Values': [subnet_id]}]
                    )
                    
                    if rt_response['RouteTables']:
                        route_table = rt_response['RouteTables'][0]
                        
                        config['SubnetDetails']['RouteTable'] = {
                            'RouteTableId': route_table['RouteTableId'],
                            'Routes': [
                                {
                                    'DestinationCidrBlock': route.get('DestinationCidrBlock', route.get('DestinationIpv6CidrBlock')),
                                    'GatewayId': route.get('GatewayId', 'N/A'),
                                    'State': route.get('State'),
                                    'Origin': route.get('Origin')
                                }
                                for route in route_table.get('Routes', [])
                            ]
                        }
                    
                    print(f"      Captured subnet details: {subnet_id}")
            
            except Exception as e:
                print(f"Could not fetch subnet details: {str(e)}")
                config['SubnetDetails'] = {'SubnetId': subnet_id, 'Error': str(e)}
        
         
        # STEP 5: NETWORK INTERFACES (ENI)
         
        config['NetworkInterfaces'] = []
        
        for ni in instance.get('NetworkInterfaces', []):
            config['NetworkInterfaces'].append({
                'NetworkInterfaceId': ni.get('NetworkInterfaceId'),
                'PrivateIpAddress': ni.get('PrivateIpAddress'),
                'PrivateIpAddresses': ni.get('PrivateIpAddresses', []),
                'SubnetId': ni.get('SubnetId'),
                'VpcId': ni.get('VpcId'),
                'Description': ni.get('Description'),
                'SourceDestCheck': ni.get('SourceDestCheck'),
                'Groups': ni.get('Groups', [])
            })
        
         
        # STEP 6: SAVE TO S3
         
        # Convert to JSON string with proper formatting
        config_json = json.dumps(config, indent=2, default=str)
        
        # Upload to S3
        s3_key = f'ec2-configs/{instance_id}/instance-config-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=config_json,
            ContentType='application/json'
        )
        
        print(f"    Enhanced backup saved to s3://{S3_BUCKET}/{s3_key}")
        print(f"    Backup includes: Instance + Security Groups + VPC + Subnet + Routes")
        
        return True
    
    except Exception as e:
        print(f"    Backup failed: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


def send_idle_alert(instance, instance_id, cpu_usage):
    """
    Sends SNS notification about idle instance
    """
    
    try:
        # Get instance name from tags
        instance_name = "Unnamed"
        for tag in instance.get('Tags', []):
            if tag['Key'] == 'Name':
                instance_name = tag['Value']
                break
        
        # Compose email message
        subject = f"  CostGuardian Alert - Idle EC2 Detected: {instance_name}"
        
        message = f"""
                    CostGuardian has detected an idle EC2 instance:

                    Instance Details:
                    - ID: {instance_id}
                    - Name: {instance_name}
                    - Type: {instance.get('InstanceType')}
                    - State: {instance['State']['Name']}
                    - Average CPU (24h): {cpu_usage:.2f}%

                    Action Taken:
                      Configuration backed up to S3
                      Logged in DynamoDB

                    Next Steps:
                    - If you don't need this instance, consider stopping it
                    - Configuration is preserved: s3://{S3_BUCKET}/ec2-configs/{instance_id}/

                    Estimated Monthly Cost: ${get_instance_cost(instance.get('InstanceType'))}/month

                    -- CostGuardian Automated Alert
                    """
        
        # Publish to SNS
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    Email alert sent!")
    
    except Exception as e:
        print(f"    Failed to send alert: {str(e)}")
        
'''
def log_metric(metric_name, value, unit='Count'):
    """
    Send custom metric to CloudWatch for monitoring
    """
    try:
        cloudwatch.put_metric_data(
            Namespace='CostGuardian',
            MetricData=[{
                'MetricName': metric_name,
                'Value': value,
                'Unit': unit,
                'Timestamp': datetime.now()
            }]
        )
    except Exception as e:
        print(f"Failed to log metric {metric_name}: {str(e)}")
'''


def get_instance_cost(instance_type, region='us-east-1'):
    
    
    pricing = {
        # T2 Family (Burstable)
        't2.nano': 4.25,
        't2.micro': 8.50,
        't2.small': 17.00,
        't2.medium': 33.87,
        't2.large': 67.74,
        't2.xlarge': 135.48,
        't2.2xlarge': 270.96,
        
        # T3 Family (Burstable, better performance)
        't3.nano': 3.80,
        't3.micro': 7.59,
        't3.small': 15.18,
        't3.medium': 30.37,
        't3.large': 60.74,
        't3.xlarge': 121.47,
        't3.2xlarge': 242.94,
        
        # M5 Family (General Purpose)
        'm5.large': 69.35,
        'm5.xlarge': 138.70,
        'm5.2xlarge': 277.40,
        'm5.4xlarge': 554.80,
        
        # C5 Family (Compute Optimized)
        'c5.large': 61.32,
        'c5.xlarge': 122.63,
        'c5.2xlarge': 245.26,
        
        # R5 Family (Memory Optimized)
        'r5.large': 90.40,
        'r5.xlarge': 180.79,
        'r5.2xlarge': 361.58,
    }
    
    estimated_cost = pricing.get(instance_type, None)
    
    if estimated_cost:
        return estimated_cost
    else:
        # Unknown type - estimate based on naming convention
        print(f"     Unknown instance type: {instance_type}")
        if 'nano' in instance_type:
            return 5
        elif 'micro' in instance_type:
            return 10
        elif 'small' in instance_type:
            return 20
        elif 'medium' in instance_type:
            return 35
        elif 'large' in instance_type:
            return 70
        elif 'xlarge' in instance_type and '2x' not in instance_type:
            return 140
        else:
            return 200  # Conservative estimate for large instances

 
# HELPER FUNCTION: GET RESOURCE HISTORY
 
def get_resource_history(table, resource_id, days=30):
    """
    Retrieves the history of a resource from DynamoDB
    Returns list of status entries, sorted by timestamp (oldest first)
    """
    try:
        from boto3.dynamodb.conditions import Key
        
        # Calculate timestamp for X days ago
        cutoff_timestamp = int((datetime.now() - timedelta(days=days)).timestamp())
        
        # Query DynamoDB for this resource's history
        response = table.query(
            KeyConditionExpression=Key('ResourceId').eq(resource_id) & 
                                  Key('Timestamp').gte(cutoff_timestamp),
            ScanIndexForward=True  # Sort ascending (oldest first)
        )
        
        items = response.get('Items', [])
        print(f"  📚 Found {len(items)} history entries for {resource_id}")
        
        return items
    
    except Exception as e:
        print(f"    Error getting history: {str(e)}")
        return []


 
# HELPER FUNCTION: DETERMINE ACTION (CONFIGURABLE)
 
def determine_action(history, current_cpu):
    """
    Analyzes resource history and determines what action to take
    
    Decision Logic (configurable):
    - If CPU >= 5%: ACTIVE (no action)
    - If first time idle: WARN (backup + email)
    - If idle IDLE_CHECKS_BEFORE_ACTION times:
      * If SKIP_QUARANTINE=True: DELETE immediately
      * If SKIP_QUARANTINE=False: QUARANTINE (stop instance)
    - If quarantined GRACE_PERIOD_DAYS days: DELETE
    
    Returns: (action, idle_count, quarantine_date)
    Actions: 'ACTIVE', 'WARN', 'QUARANTINE', 'DELETE'
    """
    
    # If currently active (CPU high), return immediately
    if current_cpu >= CPU_IDLE_THRESHOLD:
        return 'ACTIVE', 0, None
    
    # Count consecutive idle entries
    idle_count = 0
    quarantine_date = None
    last_active_found = False
    
    # Scan history from newest to oldest
    for entry in reversed(history):
        status = entry.get('Status', 'UNKNOWN')
        
        if status == 'ACTIVE':
            # Found recent activity, stop counting
            last_active_found = True
            break
        
        elif status in ['IDLE', 'IDLE_WARNING']:
            # Count idle detections
            idle_count += 1
        
        elif status == 'QUARANTINE':
            # Instance is already quarantined
            quarantine_date_raw = entry.get('Timestamp')
            if quarantine_date_raw:
                quarantine_date = float(quarantine_date_raw)
            idle_count += 1
            break
    
    # Decision tree
    if quarantine_date:
        # Instance is quarantined - check if grace period expired
        current_time = datetime.now().timestamp()
        days_since_quarantine = (current_time - quarantine_date) / 86400
        
        print(f"  ⏱️  Quarantined for {days_since_quarantine:.1f} days (grace period: {GRACE_PERIOD_DAYS} days)")
        
        if days_since_quarantine >= GRACE_PERIOD_DAYS:
            return 'DELETE', idle_count, quarantine_date
        else:
            return 'QUARANTINE', idle_count, quarantine_date
    
    elif idle_count >= IDLE_CHECKS_BEFORE_ACTION:
        # Idle for required number of checks
        print(f"    Idle threshold reached: {idle_count} consecutive checks")
        
        if SKIP_QUARANTINE:
            # Skip stopping, go straight to deletion
            print(f"  SKIP_QUARANTINE=True: Will delete immediately without stopping")
            return 'DELETE', idle_count, None
        else:
            # Normal flow: stop first (quarantine)
            return 'QUARANTINE', idle_count, None
    
    elif idle_count >= 1:
        # First or second idle detection - just warn
        return 'WARN', idle_count, None
    
    else:
        # First time seeing this resource or no idle history
        return 'WARN', 0, None

 
# HELPER FUNCTION: STOP EC2 INSTANCE
 
def stop_instance(instance_id, instance):
    """
    Stops an EC2 instance (does not terminate)
    Creates AMI backup first for safety
    
    Returns: AMI ID if successful, None if failed
    """
    try:
        print(f"    Initiating STOP sequence for {instance_id}...")
        
        # Get instance name from tags
        instance_name = "Unnamed"
        for tag in instance.get('Tags', []):
            if tag['Key'] == 'Name':
                instance_name = tag['Value']
                break
        
        # Step 1: Create AMI backup before stopping
        ami_name = f"CostGuardian-Backup-{instance_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        ami_description = f"CostGuardian automatic backup of {instance_name} before stopping"
        
        print(f"    Creating AMI backup: {ami_name}")
        
        ami_response = ec2_client.create_image(
            InstanceId=instance_id,
            Name=ami_name,
            Description=ami_description,
            NoReboot=True,  # Don't reboot instance (minimizes disruption)
            TagSpecifications=[
                {
                    'ResourceType': 'image',
                    'Tags': [
                        {'Key': 'CostGuardian', 'Value': 'AutoBackup'},
                        {'Key': 'OriginalInstanceId', 'Value': instance_id},
                        {'Key': 'OriginalInstanceName', 'Value': instance_name},
                        {'Key': 'BackupDate', 'Value': datetime.now().isoformat()}
                    ]
                }
            ]
        )
        
        ami_id = ami_response['ImageId']
        print(f"    AMI created successfully: {ami_id}")
        
        # Step 2: Stop the instance
        print(f"    Stopping instance {instance_id}...")
        
        ec2_client.stop_instances(
            InstanceIds=[instance_id]
        )
        
        print(f"    Instance {instance_id} stopped successfully")
        print(f"    Cost reduced from ~${get_instance_cost(instance.get('InstanceType'))}/month to ~$1-2/month")
        
        return ami_id
    
    except Exception as e:
        print(f"    Error stopping instance: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return None


 
# HELPER FUNCTION: TERMINATE EC2 INSTANCE
 
def terminate_instance(instance_id):
    """
    Terminates (permanently deletes) an EC2 instance
    Should only be called after:
    1. Grace period has expired
    2. Backups are verified in S3
    3. AMI backup exists
    
    Returns: True if successful, False if failed
    """
    try:
        print(f"    Initiating TERMINATE sequence for {instance_id}...")
        
        # Terminate the instance
        response = ec2_client.terminate_instances(
            InstanceIds=[instance_id]
        )
        
        # Check response
        current_state = response['TerminatingInstances'][0]['CurrentState']['Name']
        
        print(f"    Instance {instance_id} termination initiated")
        print(f"    Current state: {current_state}")
        print(f"     This action is PERMANENT - instance cannot be restarted")
        print(f"    All backups preserved in S3 and AMI")
        
        return True
    
    except Exception as e:
        print(f"    Error terminating instance: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


 
# HELPER FUNCTION: SEND QUARANTINE ALERT
 
def send_quarantine_alert(instance, instance_id, ami_id, idle_count):
    """
    Sends email notification when instance is stopped (quarantined)
    """
    try:
        instance_name = "Unnamed"
        for tag in instance.get('Tags', []):
            if tag['Key'] == 'Name':
                instance_name = tag['Value']
                break
        
        subject = f"  CostGuardian Alert - Instance STOPPED: {instance_name}"
        
        estimated_cost = get_instance_cost(instance.get('InstanceType'))
        grace_end_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M UTC')
        
        message = f"""
CostGuardian has STOPPED an idle EC2 instance:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTANCE DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Instance ID:      {instance_id}
Instance Name:    {instance_name}
Instance Type:    {instance.get('InstanceType')}
Region:           {ec2_client.meta.region_name}

Status:           STOPPED  
Idle Duration:    {idle_count} consecutive checks (~{idle_count} hours)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS TAKEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Instance STOPPED (not terminated)
  AMI backup created: {ami_id}
  Configuration saved to S3
  DynamoDB history updated

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Before (Running):  ${estimated_cost:.2f}/month
After (Stopped):   ~$1-2/month (EBS volumes + AMI storage)

Monthly Savings:   ${estimated_cost - 1.50:.2f} (~{((estimated_cost - 1.50) / estimated_cost * 100):.0f}% reduction)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GRACE PERIOD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Duration:          7 days
Deletion Date:     {grace_end_date}

If you need this instance:
→ Restart it from EC2 Console before deletion date
→ Or tag it with: CostGuardian=Ignore

If still stopped after grace period:
→ Instance will be TERMINATED (permanent)
→ All backups will be preserved for recovery

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BACKUPS LOCATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
S3 Config:   s3://{S3_BUCKET}/ec2-configs/{instance_id}/
AMI Backup:  {ami_id}
DynamoDB:    Table CostGuardianResourceLogs

To restore this instance from backup:
1. Go to EC2 Console → AMIs
2. Find AMI: {ami_id}
3. Right-click → Launch instance from AMI

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This is an automated alert from CostGuardian.
To disable monitoring for this instance, add tag: CostGuardian=Ignore

-- CostGuardian Cost Optimization System
"""
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    Quarantine alert email sent!")
        
    except Exception as e:
        print(f"    Failed to send quarantine alert: {str(e)}")


 
# HELPER FUNCTION: SEND DELETION ALERT
 
def send_deletion_alert(instance, instance_id, deleted_sgs=None):
    """
    Sends email notification when instance is terminated
    Now includes security group cleanup information
    """
    try:
        instance_name = "Unnamed"
        for tag in instance.get('Tags', []):
            if tag['Key'] == 'Name':
                instance_name = tag['Value']
                break
        
        subject = f"🗑️ CostGuardian Alert - Instance DELETED: {instance_name}"
        
        estimated_savings = get_instance_cost(instance.get('InstanceType'))
        annual_savings = estimated_savings * 12
        
        # Build security group cleanup section
        sg_section = ""
        if deleted_sgs and len(deleted_sgs) > 0:
            sg_section = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nSECURITY GROUPS CLEANED UP\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            for sg in deleted_sgs:
                sg_section += f"  Deleted: {sg['GroupName']} ({sg['GroupId']})\n"
            sg_section += "\nOrphaned security groups have been removed.\n"
        else:
            sg_section = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nSECURITY GROUPS\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nNo orphaned security groups to clean up.\n(Either shared with other instances or default SG)\n"
        
        message = f"""
CostGuardian has TERMINATED an idle EC2 instance:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTANCE DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Instance ID:      {instance_id}
Instance Name:    {instance_name}
Instance Type:    {instance.get('InstanceType')}
Status:           TERMINATED  (PERMANENT)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST SAVINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Monthly Savings:  ${estimated_savings:.2f}
  Annual Savings:   ${annual_savings:.2f}

This instance was idle for 7+ days and has been permanently deleted.
{sg_section}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR BACKUPS (PRESERVED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All configuration and data backups are preserved:

📁 S3 Configuration Backup:
   s3://{S3_BUCKET}/ec2-configs/{instance_id}/
   
   Contains:
   • Complete instance configuration
   • Security group rules (saved before deletion)
   • VPC and subnet details
   • Network configuration
   • Tags and metadata

📁 AMI Snapshots:
   EC2 Console → AMIs → Filter by tag "OriginalInstanceId: {instance_id}"
   
   Retention: 90 days (configurable)

📁 DynamoDB History:
   Complete audit trail in CostGuardianResourceLogs table

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TO RECREATE THIS INSTANCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Option 1: From AMI (Fastest)
  1. EC2 Console → AMIs
  2. Find backup AMI for {instance_id}
  3. Right-click → Launch instance from AMI
  4. Security groups will need to be recreated from S3 config

Option 2: From S3 Configuration (Manual)
  1. Download config from S3
  2. Recreate security groups from saved rules
  3. Create new instance with same settings
  4. Apply security groups and network config

Option 3: Infrastructure as Code
  1. Use saved Terraform/CloudFormation templates
  2. Deploy with: terraform apply

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   Note: This instance is permanently deleted and cannot be restarted.
  All backups remain available for recreation if needed.

-- CostGuardian Cost Optimization System
"""
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    Deletion alert email sent (including SG cleanup info)!")
        
    except Exception as e:
        print(f"    Failed to send deletion alert: {str(e)}")

 
# HELPER FUNCTION: DELETE SECURITY GROUPS
 
def delete_security_groups(instance):
    """
    Deletes security groups associated with a terminated instance
    Only deletes if:
    1. Security group is not 'default'
    2. No other instances are using it
    3. Not attached to other network interfaces
    
    Returns: List of deleted security group IDs
    """
    deleted_sgs = []
    
    try:
        security_groups = instance.get('SecurityGroups', [])
        
        if not security_groups:
            print(f"  ℹ️  No security groups to clean up")
            return deleted_sgs
        
        for sg in security_groups:
            sg_id = sg['GroupId']
            sg_name = sg['GroupName']
            
            # Skip default security group (can't be deleted)
            if sg_name == 'default':
                print(f"     Skipping default security group: {sg_id}")
                continue
            
            try:
                # Check if security group is still in use
                sg_details = ec2_client.describe_security_groups(
                    GroupIds=[sg_id]
                )
                
                if not sg_details['SecurityGroups']:
                    print(f"  ℹ️  Security group {sg_id} already deleted")
                    continue
                
                # Check if any instances are still using this SG
                instances_response = ec2_client.describe_instances(
                    Filters=[
                        {
                            'Name': 'instance.group-id',
                            'Values': [sg_id]
                        },
                        {
                            'Name': 'instance-state-name',
                            'Values': ['running', 'stopped', 'stopping', 'pending']
                        }
                    ]
                )
                
                # Count instances using this security group
                instance_count = sum(
                    len(reservation['Instances']) 
                    for reservation in instances_response['Reservations']
                )
                
                if instance_count > 0:
                    print(f"     Security group {sg_name} ({sg_id}) still in use by {instance_count} instance(s)")
                    continue
                
                # Check if any network interfaces are using this SG
                ni_response = ec2_client.describe_network_interfaces(
                    Filters=[
                        {
                            'Name': 'group-id',
                            'Values': [sg_id]
                        }
                    ]
                )
                
                if ni_response['NetworkInterfaces']:
                    print(f"     Security group {sg_name} ({sg_id}) attached to {len(ni_response['NetworkInterfaces'])} network interface(s)")
                    continue
                
                # Safe to delete - no dependencies
                print(f"    Deleting security group: {sg_name} ({sg_id})")
                
                ec2_client.delete_security_group(GroupId=sg_id)
                
                deleted_sgs.append({
                    'GroupId': sg_id,
                    'GroupName': sg_name
                })
                
                print(f"    Security group {sg_name} deleted successfully")
            
            except ec2_client.exceptions.ClientError as e:
                error_code = e.response['Error']['Code']
                
                if error_code == 'DependencyViolation':
                    print(f"     Cannot delete {sg_name} ({sg_id}): Still has dependencies")
                elif error_code == 'InvalidGroup.NotFound':
                    print(f"  ℹ️  Security group {sg_id} already deleted")
                else:
                    print(f"    Error deleting {sg_name} ({sg_id}): {str(e)}")
            
            except Exception as e:
                print(f"    Unexpected error with {sg_id}: {str(e)}")
        
        return deleted_sgs
    
    except Exception as e:
        print(f"    Error in security group cleanup: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return deleted_sgs

 
# HELPER FUNCTION: GET ALL NAT GATEWAYS
 
def get_all_nat_gateways():
    """
    Retrieves all NAT Gateways in the account/region
    Returns list of NAT Gateway details
    """
    try:
        print(f"\n🌐 Scanning for NAT Gateways...")
        
        # Get all NAT Gateways
        response = ec2_client.describe_nat_gateways(
            Filters=[
                {
                    'Name': 'state',
                    'Values': ['available', 'pending']  # Skip deleted/failed
                }
            ]
        )
        
        nat_gateways = response.get('NatGateways', [])
        
        print(f"  Found {len(nat_gateways)} NAT Gateway(s)")
        
        return nat_gateways
    
    except Exception as e:
        print(f"  Error getting NAT Gateways: {str(e)}")
        return []


 
# HELPER FUNCTION: CHECK NAT GATEWAY USAGE
 
def check_nat_gateway_usage(nat_gateway_id):
    """
    Checks CloudWatch metrics to determine if NAT Gateway is being used
    
    Metrics checked:
    - BytesOutToDestination (data sent to internet)
    - BytesInFromDestination (data received from internet)
    
    Returns: (bytes_out, bytes_in, is_idle)
    """
    try:
        print(f"    Checking usage metrics for {nat_gateway_id}...")
        
        # Check last 7 days of usage
        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
        
        # Metric 1: Bytes sent to internet
        bytes_out_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/NATGateway',
            MetricName='BytesOutToDestination',
            Dimensions=[
                {
                    'Name': 'NatGatewayId',
                    'Value': nat_gateway_id
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,  # 1 day periods
            Statistics=['Sum']
        )
        
        # Metric 2: Bytes received from internet
        bytes_in_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/NATGateway',
            MetricName='BytesInFromDestination',
            Dimensions=[
                {
                    'Name': 'NatGatewayId',
                    'Value': nat_gateway_id
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=['Sum']
        )
        
        # Calculate total bytes over 7 days
        bytes_out_datapoints = bytes_out_response.get('Datapoints', [])
        bytes_in_datapoints = bytes_in_response.get('Datapoints', [])
        
        total_bytes_out = sum(dp['Sum'] for dp in bytes_out_datapoints)
        total_bytes_in = sum(dp['Sum'] for dp in bytes_in_datapoints)
        
        # Convert to MB for readability
        mb_out = total_bytes_out / 1024 / 1024
        mb_in = total_bytes_in / 1024 / 1024
        
        print(f"  📤 Bytes Out (7 days): {mb_out:.2f} MB")
        print(f"  📥 Bytes In (7 days): {mb_in:.2f} MB")
        
        # Determine if idle (< 1 MB total in 7 days = basically unused)
        total_mb = mb_out + mb_in
        is_idle = total_mb < 1.0  # Less than 1 MB in a week = idle
        
        if is_idle:
            print(f"     NAT Gateway appears IDLE (< 1 MB traffic in 7 days)")
        else:
            print(f"    NAT Gateway is ACTIVE ({total_mb:.2f} MB transferred)")
        
        return total_bytes_out, total_bytes_in, is_idle
    
    except Exception as e:
        print(f"    Error checking metrics: {str(e)}")
        # If can't get metrics, assume active (safer)
        return 0, 0, False


 
# HELPER FUNCTION: BACKUP NAT GATEWAY CONFIG
 
def backup_nat_gateway_config(nat_gateway):
    """
    Backs up complete NAT Gateway configuration to S3
    Includes: VPC, subnet, route tables, Elastic IP details
    """
    try:
        nat_gateway_id = nat_gateway['NatGatewayId']
        
        print(f"    Backing up NAT Gateway configuration...")
        
        # Basic NAT Gateway details
        config = {
            'NatGatewayId': nat_gateway_id,
            'State': nat_gateway['State'],
            'SubnetId': nat_gateway['SubnetId'],
            'VpcId': nat_gateway['VpcId'],
            'CreateTime': str(nat_gateway['CreateTime']),
            'NatGatewayAddresses': nat_gateway.get('NatGatewayAddresses', []),
            'Tags': nat_gateway.get('Tags', []),
            'BackupTimestamp': datetime.now().isoformat()
        }
        
        # Get Elastic IP details
        for address in nat_gateway.get('NatGatewayAddresses', []):
            allocation_id = address.get('AllocationId')
            if allocation_id:
                config['ElasticIP'] = {
                    'AllocationId': allocation_id,
                    'PublicIp': address.get('PublicIp'),
                    'PrivateIp': address.get('PrivateIp'),
                    'NetworkInterfaceId': address.get('NetworkInterfaceId')
                }
        
        # Get Subnet details
        subnet_id = nat_gateway['SubnetId']
        try:
            subnet_response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
            if subnet_response['Subnets']:
                subnet = subnet_response['Subnets'][0]
                config['SubnetDetails'] = {
                    'SubnetId': subnet_id,
                    'AvailabilityZone': subnet.get('AvailabilityZone'),
                    'CidrBlock': subnet.get('CidrBlock'),
                    'Tags': subnet.get('Tags', [])
                }
        except Exception as e:
            print(f"     Could not fetch subnet details: {str(e)}")
        
        # Get VPC details
        vpc_id = nat_gateway['VpcId']
        try:
            vpc_response = ec2_client.describe_vpcs(VpcIds=[vpc_id])
            if vpc_response['Vpcs']:
                vpc = vpc_response['Vpcs'][0]
                config['VpcDetails'] = {
                    'VpcId': vpc_id,
                    'CidrBlock': vpc.get('CidrBlock'),
                    'Tags': vpc.get('Tags', [])
                }
        except Exception as e:
            print(f"     Could not fetch VPC details: {str(e)}")
        
        # Get Route Tables that reference this NAT Gateway
        try:
            rt_response = ec2_client.describe_route_tables(
                Filters=[
                    {
                        'Name': 'route.nat-gateway-id',
                        'Values': [nat_gateway_id]
                    }
                ]
            )
            
            config['RouteTables'] = []
            for rt in rt_response.get('RouteTables', []):
                config['RouteTables'].append({
                    'RouteTableId': rt['RouteTableId'],
                    'VpcId': rt['VpcId'],
                    'Routes': [
                        {
                            'DestinationCidrBlock': route.get('DestinationCidrBlock', 'N/A'),
                            'NatGatewayId': route.get('NatGatewayId'),
                            'State': route.get('State')
                        }
                        for route in rt.get('Routes', [])
                        if route.get('NatGatewayId') == nat_gateway_id
                    ],
                    'Associations': [
                        {
                            'SubnetId': assoc.get('SubnetId'),
                            'Main': assoc.get('Main', False)
                        }
                        for assoc in rt.get('Associations', [])
                    ]
                })
            
            print(f"    Found {len(config['RouteTables'])} route table(s) using this NAT Gateway")
        
        except Exception as e:
            print(f"     Could not fetch route tables: {str(e)}")
            config['RouteTables'] = []
        
        # Calculate monthly cost
        config['EstimatedMonthlyCost'] = 32.40  # Base NAT Gateway cost
        
        # Save to S3
        config_json = json.dumps(config, indent=2, default=str)
        
        s3_key = f'nat-gateway-configs/{nat_gateway_id}/config-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=config_json,
            ContentType='application/json'
        )
        
        print(f"    NAT Gateway configuration backed up to S3")
        print(f"  📁 s3://{S3_BUCKET}/{s3_key}")
        
        return True
    
    except Exception as e:
        print(f"    Backup failed: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


 
# HELPER FUNCTION: DELETE NAT GATEWAY
 
def delete_nat_gateway(nat_gateway_id, release_eip=True):
    """
    Deletes a NAT Gateway and optionally releases the Elastic IP
    
    Steps:
    1. Remove routes pointing to NAT Gateway (update route tables)
    2. Delete NAT Gateway
    3. Wait for deletion to complete
    4. Release Elastic IP (if requested)
    
    Returns: (success, elastic_ip_released)
    """
    try:
        print(f"    Initiating NAT Gateway deletion: {nat_gateway_id}")
        
        # Step 1: Get Elastic IP allocation ID before deletion
        nat_gw_response = ec2_client.describe_nat_gateways(
            NatGatewayIds=[nat_gateway_id]
        )
        
        allocation_id = None
        if nat_gw_response['NatGateways']:
            nat_gw = nat_gw_response['NatGateways'][0]
            addresses = nat_gw.get('NatGatewayAddresses', [])
            if addresses:
                allocation_id = addresses[0].get('AllocationId')
                public_ip = addresses[0].get('PublicIp')
                print(f"  📝 Associated Elastic IP: {public_ip} ({allocation_id})")
        
        # Step 2: Update route tables (remove routes pointing to NAT Gateway)
        print(f"  🔧 Updating route tables...")
        try:
            rt_response = ec2_client.describe_route_tables(
                Filters=[
                    {
                        'Name': 'route.nat-gateway-id',
                        'Values': [nat_gateway_id]
                    }
                ]
            )
            
            for rt in rt_response.get('RouteTables', []):
                rt_id = rt['RouteTableId']
                print(f"    • Removing routes from {rt_id}")
                
                # Find routes using this NAT Gateway
                for route in rt.get('Routes', []):
                    if route.get('NatGatewayId') == nat_gateway_id:
                        dest_cidr = route.get('DestinationCidrBlock')
                        if dest_cidr:
                            try:
                                ec2_client.delete_route(
                                    RouteTableId=rt_id,
                                    DestinationCidrBlock=dest_cidr
                                )
                                print(f"        Removed route: {dest_cidr} → {nat_gateway_id}")
                            except Exception as e:
                                print(f"         Could not remove route {dest_cidr}: {str(e)}")
        
        except Exception as e:
            print(f"     Error updating route tables: {str(e)}")
            print(f"  ℹ️  Continuing with NAT Gateway deletion anyway...")
        
        # Step 3: Delete NAT Gateway
        print(f"    Deleting NAT Gateway...")
        
        ec2_client.delete_nat_gateway(
            NatGatewayId=nat_gateway_id
        )
        
        print(f"    NAT Gateway deletion initiated")
        print(f"    NAT Gateway will be deleted in background (takes 2-5 minutes)")
        
        # Step 4: Release Elastic IP (optional)
        eip_released = False
        if release_eip and allocation_id:
            print(f"    Waiting 30 seconds for NAT Gateway to detach from Elastic IP...")
            import time
            time.sleep(30)  # Wait for NAT Gateway to release EIP
            
            try:
                print(f"    Releasing Elastic IP: {allocation_id}")
                
                ec2_client.release_address(
                    AllocationId=allocation_id
                )
                
                print(f"    Elastic IP released successfully")
                eip_released = True
            
            except Exception as e:
                print(f"     Could not release Elastic IP: {str(e)}")
                print(f"  ℹ️  You may need to release it manually to avoid $3.60/month charge")
                eip_released = False
        
        print(f"    Savings: $32.40/month + ${3.60 if not eip_released else 0}/month EIP")
        print(f"    Annual savings: ${(32.40 + (3.60 if not eip_released else 0)) * 12:.2f}")
        
        return True, eip_released
    
    except Exception as e:
        print(f"    Error deleting NAT Gateway: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False, False


 
# HELPER FUNCTION: SEND NAT GATEWAY ALERT
 
def send_nat_gateway_alert(nat_gateway, action, bytes_out, bytes_in):
    """
    Sends email notification about NAT Gateway actions
    action: 'IDLE_WARNING' or 'DELETED'
    """
    try:
        nat_gateway_id = nat_gateway['NatGatewayId']
        subnet_id = nat_gateway['SubnetId']
        vpc_id = nat_gateway['VpcId']
        
        # Get name from tags
        nat_name = "Unnamed"
        for tag in nat_gateway.get('Tags', []):
            if tag['Key'] == 'Name':
                nat_name = tag['Value']
                break
        
        # Get Elastic IP
        public_ip = "N/A"
        for address in nat_gateway.get('NatGatewayAddresses', []):
            public_ip = address.get('PublicIp', 'N/A')
            break
        
        # Calculate usage
        mb_out = bytes_out / 1024 / 1024
        mb_in = bytes_in / 1024 / 1024
        total_mb = mb_out + mb_in
        
        if action == 'IDLE_WARNING':
            subject = f"  CostGuardian Alert - Idle NAT Gateway Detected: {nat_name}"
            
            message = f"""
CostGuardian has detected an IDLE NAT Gateway:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NAT GATEWAY DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NAT Gateway ID:   {nat_gateway_id}
Name:             {nat_name}
VPC:              {vpc_id}
Subnet:           {subnet_id}
Elastic IP:       {public_ip}
State:            {nat_gateway['State']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE (LAST 7 DAYS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Data Out:         {mb_out:.2f} MB
Data In:          {mb_in:.2f} MB
Total Transfer:   {total_mb:.2f} MB

   This NAT Gateway has transferred < 1 MB in the past 7 days!
This typically means it's unused or forgotten.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current Monthly Cost:  $32.40 (+ data transfer fees)
Annual Cost:           $388.80/year

  If deleted, you'll save $32.40/month immediately!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS TAKEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Configuration backed up to S3
  Route table details saved
  Elastic IP information preserved

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CostGuardian will check this NAT Gateway again in 24 hours.

If still idle after 3 consecutive checks (3 days):
→ NAT Gateway will be DELETED
→ Elastic IP will be released
→ Route tables will be updated
→ All configuration preserved in S3

To prevent deletion:
1. Start using the NAT Gateway (> 1 MB traffic/day), OR
2. Add tag: CostGuardian=Ignore

Backups: s3://{S3_BUCKET}/nat-gateway-configs/{nat_gateway_id}/

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'DELETED':
            subject = f"🗑️ CostGuardian Alert - NAT Gateway DELETED: {nat_name}"
            
            message = f"""
CostGuardian has DELETED an idle NAT Gateway:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NAT GATEWAY DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NAT Gateway ID:   {nat_gateway_id}
Name:             {nat_name}
VPC:              {vpc_id}
Previous IP:      {public_ip}
Status:           DELETED 

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE BEFORE DELETION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Last 7 days:      {total_mb:.2f} MB total
Status:           IDLE (< 1 MB traffic)
Idle duration:    3+ consecutive checks

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST SAVINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Monthly Savings:   $32.40
  Annual Savings:    $388.80

This NAT Gateway was costing $32.40/month even while idle!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS COMPLETED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  NAT Gateway deleted
  Route tables updated
  Elastic IP released (saves additional $3.60/month)
  Configuration preserved in S3

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR BACKUPS (PRESERVED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All configuration saved to recreate if needed:

📁 S3 Configuration:
   s3://{S3_BUCKET}/nat-gateway-configs/{nat_gateway_id}/
   
   Contains:
   • Complete NAT Gateway settings
   • VPC and subnet configuration
   • Route table associations
   • Elastic IP details
   • Recreation instructions

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TO RECREATE THIS NAT GATEWAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If you need this NAT Gateway again:

1. EC2 Console → VPC → NAT Gateways
2. Create NAT Gateway
3. Select same subnet: {subnet_id}
4. Allocate new Elastic IP
5. Update route tables from S3 backup config

Or use AWS CLI with saved configuration.

   Note: You'll get a new Elastic IP (old one was released)

-- CostGuardian Cost Optimization System
"""
        
        else:
            return  # Unknown action
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    NAT Gateway alert email sent!")
    
    except Exception as e:
        print(f"    Failed to send NAT Gateway alert: {str(e)}")
        
 
# HELPER FUNCTION: GET ALL ELASTIC IPS
 
def get_all_elastic_ips():
    """
    Retrieves all Elastic IPs in the account/region
    Returns list of Elastic IP details
    """
    try:
        print(f"\n  Scanning for Elastic IPs...")
        
        response = ec2_client.describe_addresses()
        
        addresses = response.get('Addresses', [])
        
        print(f"  Found {len(addresses)} Elastic IP(s)")
        
        return addresses
    
    except Exception as e:
        print(f"  Error getting Elastic IPs: {str(e)}")
        return []


 
# HELPER FUNCTION: CHECK ELASTIC IP STATUS
 
def is_elastic_ip_unattached(eip):
    """
    Checks if Elastic IP is unattached (not associated with any resource)
    
    Returns: (is_unattached, association_details)
    """
    
    # Check association status
    association_id = eip.get('AssociationId')
    instance_id = eip.get('InstanceId')
    network_interface_id = eip.get('NetworkInterfaceId')
    
    if not association_id and not instance_id and not network_interface_id:
        # Completely unattached
        return True, None
    else:
        # Attached to something
        return False, {
            'AssociationId': association_id,
            'InstanceId': instance_id,
            'NetworkInterfaceId': network_interface_id
        }


 
# HELPER FUNCTION: RELEASE ELASTIC IP
 
def release_elastic_ip(allocation_id, public_ip):
    """
    Releases (deletes) an unattached Elastic IP
    
    Returns: True if successful, False otherwise
    """
    try:
        print(f"    Releasing Elastic IP: {public_ip} ({allocation_id})")
        
        ec2_client.release_address(
            AllocationId=allocation_id
        )
        
        print(f"    Elastic IP {public_ip} released successfully")
        print(f"    Savings: $3.60/month = $43.20/year")
        
        return True
    
    except ec2_client.exceptions.ClientError as e:
        error_code = e.response['Error']['Code']
        
        if error_code == 'InvalidAllocationID.NotFound':
            print(f"  ℹ️  Elastic IP {allocation_id} already released")
            return True
        else:
            print(f"    Error releasing Elastic IP: {str(e)}")
            return False
    
    except Exception as e:
        print(f"    Unexpected error: {str(e)}")
        return False


 
# HELPER FUNCTION: SEND ELASTIC IP ALERT
 
def send_elastic_ip_alert(eip, action):
    """
    Sends email notification about Elastic IP actions
    action: 'IDLE_WARNING' or 'RELEASED'
    """
    try:
        allocation_id = eip.get('AllocationId')
        public_ip = eip.get('PublicIp')
        domain = eip.get('Domain', 'vpc')
        
        # Get tags
        tags = {tag['Key']: tag['Value'] for tag in eip.get('Tags', [])}
        eip_name = tags.get('Name', 'Unnamed')
        
        if action == 'IDLE_WARNING':
            subject = f"  CostGuardian Alert - Unattached Elastic IP: {public_ip}"
            
            message = f"""
CostGuardian has detected an UNATTACHED Elastic IP:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ELASTIC IP DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Public IP:        {public_ip}
Allocation ID:    {allocation_id}
Name:             {eip_name}
Domain:           {domain}
Status:           UNATTACHED  

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current Monthly Cost:  $3.60 (unattached IP charge)
Annual Cost:           $43.20/year

  AWS charges $3.60/month for unattached Elastic IPs!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CostGuardian will check this IP again in 1 hour.

If still unattached after 3 consecutive checks (3 hours):
→ Elastic IP will be RELEASED
→ You cannot get the same IP address back

To prevent release:
1. Attach it to a running instance, OR
2. Add tag: CostGuardian=Ignore

   Note: Once released, you cannot recover this specific IP address!

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'RELEASED':
            subject = f"🗑️ CostGuardian Alert - Elastic IP RELEASED: {public_ip}"
            
            message = f"""
CostGuardian has RELEASED an unattached Elastic IP:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ELASTIC IP DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Public IP:        {public_ip}
Allocation ID:    {allocation_id}
Name:             {eip_name}
Status:           RELEASED 

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST SAVINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Monthly Savings:   $3.60
  Annual Savings:    $43.20

This Elastic IP was unattached and costing $3.60/month.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPORTANT NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   This IP address ({public_ip}) is now released
   You CANNOT get this specific IP back
   If you need an Elastic IP again, you'll get a different address

If you had this IP whitelisted somewhere:
→ Update firewall rules with new IP when you allocate one
→ Update DNS records if this was for a domain

To allocate a new Elastic IP:
1. EC2 Console → Elastic IPs
2. Allocate Elastic IP address
3. You'll get a NEW IP (not the same one)

-- CostGuardian Cost Optimization System
"""
        
        else:
            return
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    Elastic IP alert email sent!")
    
    except Exception as e:
        print(f"    Failed to send Elastic IP alert: {str(e)}")
    
 
# HELPER FUNCTION: GET ALL RDS INSTANCES
 
def get_all_rds_instances():
    """
    Retrieves all RDS database instances in the account/region
    Returns list of RDS instance details
    """
    try:
        print(f"\n🗄️  Scanning for RDS Instances...")
        
        # Initialize RDS client
        rds_client = boto3.client('rds')
        
        # Get all DB instances
        response = rds_client.describe_db_instances()
        
        db_instances = response.get('DBInstances', [])
        
        print(f"  Found {len(db_instances)} RDS instance(s)")
        
        return db_instances, rds_client
    
    except Exception as e:
        print(f"  Error getting RDS instances: {str(e)}")
        return [], None


 
# HELPER FUNCTION: CHECK RDS USAGE
 
def check_rds_usage(db_instance_identifier):
    """
    Checks CloudWatch metrics to determine if RDS instance is being used
    
    Metrics checked:
    - DatabaseConnections (active connections)
    - CPUUtilization (CPU usage)
    - ReadIOPS + WriteIOPS (database activity)
    
    Returns: (avg_connections, avg_cpu, total_iops, is_idle)
    """
    try:
        print(f"    Checking usage metrics for {db_instance_identifier}...")
        
        # Check last 7 days of usage
        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
        
        # Metric 1: Database Connections
        connections_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/RDS',
            MetricName='DatabaseConnections',
            Dimensions=[
                {
                    'Name': 'DBInstanceIdentifier',
                    'Value': db_instance_identifier
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,  # 1 day periods
            Statistics=['Average', 'Maximum']
        )
        
        # Metric 2: CPU Utilization
        cpu_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/RDS',
            MetricName='CPUUtilization',
            Dimensions=[
                {
                    'Name': 'DBInstanceIdentifier',
                    'Value': db_instance_identifier
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=['Average']
        )
        
        # Metric 3: Read IOPS
        read_iops_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/RDS',
            MetricName='ReadIOPS',
            Dimensions=[
                {
                    'Name': 'DBInstanceIdentifier',
                    'Value': db_instance_identifier
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=['Sum']
        )
        
        # Metric 4: Write IOPS
        write_iops_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/RDS',
            MetricName='WriteIOPS',
            Dimensions=[
                {
                    'Name': 'DBInstanceIdentifier',
                    'Value': db_instance_identifier
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=['Sum']
        )
        
        # Calculate averages
        connection_datapoints = connections_response.get('Datapoints', [])
        cpu_datapoints = cpu_response.get('Datapoints', [])
        read_iops_datapoints = read_iops_response.get('Datapoints', [])
        write_iops_datapoints = write_iops_response.get('Datapoints', [])
        
        # Average connections
        if connection_datapoints:
            avg_connections = sum(dp['Average'] for dp in connection_datapoints) / len(connection_datapoints)
            max_connections = max(dp['Maximum'] for dp in connection_datapoints)
        else:
            avg_connections = 0
            max_connections = 0
        
        # Average CPU
        if cpu_datapoints:
            avg_cpu = sum(dp['Average'] for dp in cpu_datapoints) / len(cpu_datapoints)
        else:
            avg_cpu = 0
        
        # Total IOPS
        total_read_iops = sum(dp['Sum'] for dp in read_iops_datapoints)
        total_write_iops = sum(dp['Sum'] for dp in write_iops_datapoints)
        total_iops = total_read_iops + total_write_iops
        
        print(f"    Connections (7 days avg): {avg_connections:.2f}")
        print(f"    Max Connections: {max_connections:.0f}")
        print(f"    CPU Usage (7 days avg): {avg_cpu:.2f}%")
        print(f"    Total IOPS (7 days): {total_iops:.0f} (Read: {total_read_iops:.0f}, Write: {total_write_iops:.0f})")
        
        # Determine if idle
        # Idle criteria: < 1 connection on average AND < 100 IOPS total in 7 days
        is_idle = (avg_connections < 1.0 and total_iops < 100)
        
        if is_idle:
            print(f"     RDS Instance appears IDLE (< 1 connection avg, < 100 IOPS)")
        else:
            print(f"    RDS Instance is ACTIVE")
        
        return avg_connections, avg_cpu, total_iops, is_idle
    
    except Exception as e:
        print(f"    Error checking metrics: {str(e)}")
        # If can't get metrics, assume active (safer)
        return 0, 0, 0, False


 
# HELPER FUNCTION: GET RDS COST ESTIMATE
 
def get_rds_cost(db_instance_class, engine):
    """
    Estimates monthly cost for RDS instance
    Prices are approximate for us-east-1, single-AZ
    """
    
    # Common instance types (monthly cost in USD)
    pricing = {
        # T3 instances (burstable, cost-effective)
        'db.t3.micro': 14.61,
        'db.t3.small': 29.22,
        'db.t3.medium': 58.43,
        'db.t3.large': 116.86,
        'db.t3.xlarge': 233.71,
        'db.t3.2xlarge': 467.42,
        
        # T4g instances (ARM, cheaper)
        'db.t4g.micro': 13.14,
        'db.t4g.small': 26.28,
        'db.t4g.medium': 52.56,
        'db.t4g.large': 105.12,
        
        # M5 instances (general purpose)
        'db.m5.large': 140.16,
        'db.m5.xlarge': 280.32,
        'db.m5.2xlarge': 560.64,
        'db.m5.4xlarge': 1121.28,
        
        # R5 instances (memory optimized)
        'db.r5.large': 182.50,
        'db.r5.xlarge': 365.00,
        'db.r5.2xlarge': 730.00,
        'db.r5.4xlarge': 1460.00,
    }
    
    estimated_cost = pricing.get(db_instance_class, None)
    
    if estimated_cost:
        return estimated_cost
    else:
        # Unknown type - estimate based on size
        print(f"     Unknown RDS instance type: {db_instance_class}")
        if 'micro' in db_instance_class:
            return 15
        elif 'small' in db_instance_class:
            return 30
        elif 'medium' in db_instance_class:
            return 60
        elif 'large' in db_instance_class and 'xlarge' not in db_instance_class:
            return 150
        elif 'xlarge' in db_instance_class and '2x' not in db_instance_class:
            return 300
        else:
            return 500  # Conservative estimate for large instances


 
# HELPER FUNCTION: BACKUP RDS CONFIGURATION
 
def backup_rds_config(db_instance, rds_client):
    """
    Backs up complete RDS instance configuration to S3
    Includes: parameter groups, subnet groups, security groups, options
    """
    try:
        db_instance_identifier = db_instance['DBInstanceIdentifier']
        
        print(f"    Backing up RDS configuration...")
        
        # Basic DB instance details
        config = {
            'DBInstanceIdentifier': db_instance_identifier,
            'DBInstanceClass': db_instance.get('DBInstanceClass'),
            'Engine': db_instance.get('Engine'),
            'EngineVersion': db_instance.get('EngineVersion'),
            'DBInstanceStatus': db_instance.get('DBInstanceStatus'),
            'MasterUsername': db_instance.get('MasterUsername'),
            'DBName': db_instance.get('DBName'),
            'Endpoint': db_instance.get('Endpoint', {}),
            'AllocatedStorage': db_instance.get('AllocatedStorage'),
            'StorageType': db_instance.get('StorageType'),
            'Iops': db_instance.get('Iops'),
            'StorageEncrypted': db_instance.get('StorageEncrypted'),
            'KmsKeyId': db_instance.get('KmsKeyId'),
            'AvailabilityZone': db_instance.get('AvailabilityZone'),
            'MultiAZ': db_instance.get('MultiAZ'),
            'PubliclyAccessible': db_instance.get('PubliclyAccessible'),
            'VpcSecurityGroups': db_instance.get('VpcSecurityGroups', []),
            'DBSubnetGroup': db_instance.get('DBSubnetGroup', {}),
            'DBParameterGroups': db_instance.get('DBParameterGroups', []),
            'OptionGroupMemberships': db_instance.get('OptionGroupMemberships', []),
            'BackupRetentionPeriod': db_instance.get('BackupRetentionPeriod'),
            'PreferredBackupWindow': db_instance.get('PreferredBackupWindow'),
            'PreferredMaintenanceWindow': db_instance.get('PreferredMaintenanceWindow'),
            'LatestRestorableTime': str(db_instance.get('LatestRestorableTime', '')),
            'AutoMinorVersionUpgrade': db_instance.get('AutoMinorVersionUpgrade'),
            'LicenseModel': db_instance.get('LicenseModel'),
            'Tags': db_instance.get('TagList', []),
            'BackupTimestamp': datetime.now().isoformat()
        }
        
        # Calculate estimated cost
        config['EstimatedMonthlyCost'] = get_rds_cost(
            db_instance.get('DBInstanceClass'),
            db_instance.get('Engine')
        )
        
        # Save to S3
        config_json = json.dumps(config, indent=2, default=str)
        
        s3_key = f'rds-configs/{db_instance_identifier}/config-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=config_json,
            ContentType='application/json'
        )
        
        print(f"    RDS configuration backed up to S3")
        print(f"  📁 s3://{S3_BUCKET}/{s3_key}")
        
        return True
    
    except Exception as e:
        print(f"    Backup failed: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


 
# HELPER FUNCTION: CREATE RDS SNAPSHOT
 
def create_rds_snapshot(db_instance_identifier, rds_client):
    """
    Creates a final snapshot of the RDS instance before deletion
    
    Returns: snapshot_identifier if successful, None otherwise
    """
    try:
        snapshot_id = f"costguardian-final-{db_instance_identifier}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        print(f"    Creating final RDS snapshot: {snapshot_id}")
        
        response = rds_client.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=db_instance_identifier,
            Tags=[
                {
                    'Key': 'CostGuardian',
                    'Value': 'AutoSnapshot'
                },
                {
                    'Key': 'OriginalInstance',
                    'Value': db_instance_identifier
                },
                {
                    'Key': 'CreatedDate',
                    'Value': datetime.now().isoformat()
                }
            ]
        )
        
        print(f"    Snapshot creation initiated: {snapshot_id}")
        print(f"    Snapshot will complete in background (5-15 minutes depending on size)")
        
        return snapshot_id
    
    except Exception as e:
        print(f"    Snapshot creation failed: {str(e)}")
        return None


 
# HELPER FUNCTION: STOP RDS INSTANCE
 
def stop_rds_instance(db_instance_identifier, rds_client):
    """
    Stops an RDS instance (does not delete)
    
    Note: AWS automatically starts stopped RDS instances after 7 days!
    
    Returns: True if successful, False otherwise
    """
    try:
        print(f"    Stopping RDS instance: {db_instance_identifier}")
        
        rds_client.stop_db_instance(
            DBInstanceIdentifier=db_instance_identifier
        )
        
        print(f"    RDS instance stop initiated")
        print(f"    Instance will stop in 2-5 minutes")
        print(f"     AWS will AUTO-START this instance after 7 days!")
        
        return True
    
    except Exception as e:
        error_message = str(e)
        
        if 'InvalidDBInstanceState' in error_message:
            print(f"  ℹ️  Instance already stopped or stopping")
            return True
        else:
            print(f"    Error stopping instance: {error_message}")
            return False


 
# HELPER FUNCTION: DELETE RDS INSTANCE
 
def delete_rds_instance(db_instance_identifier, snapshot_id, rds_client):
    """
    Deletes an RDS instance permanently
    Should only be called after final snapshot is created
    
    Returns: True if successful, False otherwise
    """
    try:
        print(f"    Deleting RDS instance: {db_instance_identifier}")
        
        # Delete with final snapshot (we already created one, so skip automatic)
        rds_client.delete_db_instance(
            DBInstanceIdentifier=db_instance_identifier,
            SkipFinalSnapshot=True,  # We created our own snapshot already
            DeleteAutomatedBackups=False  # Keep automated backups for 7 days
        )
        
        print(f"    RDS instance deletion initiated")
        print(f"    Instance will be deleted in 5-10 minutes")
        print(f"     This action is PERMANENT - instance cannot be restarted")
        print(f"    Snapshot preserved: {snapshot_id}")
        
        return True
    
    except Exception as e:
        print(f"    Error deleting instance: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


 
# HELPER FUNCTION: SEND RDS ALERT
 
def send_rds_alert(db_instance, action, avg_connections, avg_cpu, total_iops, snapshot_id=None):
    """
    Sends email notification about RDS actions
    action: 'IDLE_WARNING', 'STOPPED', or 'DELETED'
    """
    try:
        db_instance_identifier = db_instance['DBInstanceIdentifier']
        db_instance_class = db_instance.get('DBInstanceClass')
        engine = db_instance.get('Engine')
        engine_version = db_instance.get('EngineVersion')
        
        # Get name from tags
        db_name = "Unnamed"
        for tag in db_instance.get('TagList', []):
            if tag['Key'] == 'Name':
                db_name = tag['Value']
                break
        
        estimated_cost = get_rds_cost(db_instance_class, engine)
        
        if action == 'IDLE_WARNING':
            subject = f"  CostGuardian Alert - Idle RDS Database: {db_name}"
            
            message = f"""
CostGuardian has detected an IDLE RDS database instance:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATABASE DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Instance ID:      {db_instance_identifier}
Name:             {db_name}
Class:            {db_instance_class}
Engine:           {engine} {engine_version}
Status:           {db_instance.get('DBInstanceStatus')}
Storage:          {db_instance.get('AllocatedStorage')} GB

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE (LAST 7 DAYS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Avg Connections:  {avg_connections:.2f}
Avg CPU:          {avg_cpu:.2f}%
Total IOPS:       {total_iops:.0f}

   This database has minimal activity!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current Monthly Cost:  ${estimated_cost:.2f}
Annual Cost:           ${estimated_cost * 12:.2f}

  If deleted, you'll save ${estimated_cost:.2f}/month!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS TAKEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Configuration backed up to S3

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CostGuardian will check again in 24 hours.

If still idle after 3 consecutive checks (3 days):
→ Database will be STOPPED
→ Final snapshot created
→ Grace period: 7 days before deletion

To prevent deletion:
1. Start using the database (> 1 connection/day), OR
2. Add tag: CostGuardian=Ignore

Backups: s3://{S3_BUCKET}/rds-configs/{db_instance_identifier}/

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'STOPPED':
            subject = f"  CostGuardian Alert - RDS Database STOPPED: {db_name}"
            
            message = f"""
CostGuardian has STOPPED an idle RDS database:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATABASE DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Instance ID:      {db_instance_identifier}
Name:             {db_name}
Class:            {db_instance_class}
Engine:           {engine} {engine_version}
Status:           STOPPED  

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE BEFORE STOPPING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Avg Connections:  {avg_connections:.2f}
Avg CPU:          {avg_cpu:.2f}%
Total IOPS:       {total_iops:.0f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS COMPLETED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Database STOPPED
  Final snapshot created: {snapshot_id}
  Configuration saved to S3

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Before (Running):  ${estimated_cost:.2f}/month
After (Stopped):   ~${estimated_cost * 0.2:.2f}/month (storage only)

Monthly Savings:   ${estimated_cost * 0.8:.2f} (~80% reduction)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPORTANT NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   AWS will AUTO-START this database after 7 days!
   To prevent this, delete it or start it manually

Grace Period: 7 days
Deletion Date: {(datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')}

If still stopped after 7 days → Will be DELETED

To restart database:
1. RDS Console → Databases
2. Select {db_instance_identifier}
3. Actions → Start

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'DELETED':
            subject = f"🗑️ CostGuardian Alert - RDS Database DELETED: {db_name}"
            
            message = f"""
CostGuardian has DELETED an idle RDS database:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATABASE DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Instance ID:      {db_instance_identifier}
Name:             {db_name}
Class:            {db_instance_class}
Engine:           {engine} {engine_version}
Status:           DELETED  (PERMANENT)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST SAVINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Monthly Savings:  ${estimated_cost:.2f}
  Annual Savings:   ${estimated_cost * 12:.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR BACKUPS (PRESERVED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 S3 Configuration:
   s3://{S3_BUCKET}/rds-configs/{db_instance_identifier}/
   
   Contains: Complete database configuration

📁 RDS Snapshot:
   Snapshot ID: {snapshot_id}
   Location: RDS Console → Snapshots
   Retention: Until manually deleted
   
      Snapshot storage costs ~$0.095/GB/month

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TO RESTORE THIS DATABASE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. RDS Console → Snapshots
2. Find snapshot: {snapshot_id}
3. Actions → Restore snapshot
4. Configure settings from S3 backup
5. Launch restored database

-- CostGuardian Cost Optimization System
"""
        
        else:
            return
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    RDS alert email sent!")
    
    except Exception as e:
        print(f"    Failed to send RDS alert: {str(e)}")
        
 
# HELPER FUNCTION: GET ALL S3 BUCKETS
 
def get_all_s3_buckets():
    """
    Retrieves all S3 buckets in the account
    Returns list of bucket names
    """
    try:
        print(f"\n🗄️  Scanning for S3 Buckets...")
        
        response = s3_client.list_buckets()
        
        buckets = response.get('Buckets', [])
        
        print(f"  Found {len(buckets)} S3 bucket(s)")
        
        return buckets
    
    except Exception as e:
        print(f"  Error getting S3 buckets: {str(e)}")
        return []


 
# HELPER FUNCTION: CHECK IF BUCKET IS EMPTY
 
def is_bucket_empty(bucket_name):
    """
    Checks if an S3 bucket is completely empty
    
    Returns: (is_empty, object_count, total_size_mb)
    """
    try:
        # List first 1000 objects
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            MaxKeys=1000
        )
        
        contents = response.get('Contents', [])
        object_count = len(contents)
        
        # Calculate total size
        total_size_bytes = sum(obj.get('Size', 0) for obj in contents)
        total_size_mb = total_size_bytes / 1024 / 1024
        
        # Check if there are more objects
        is_truncated = response.get('IsTruncated', False)
        
        if is_truncated:
            # Bucket has 1000+ objects, definitely not empty
            # Get more accurate count
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket_name)
            
            total_objects = 0
            total_bytes = 0
            for page in pages:
                contents = page.get('Contents', [])
                total_objects += len(contents)
                total_bytes += sum(obj.get('Size', 0) for obj in contents)
            
            return False, total_objects, total_bytes / 1024 / 1024
        
        is_empty = (object_count == 0)
        
        return is_empty, object_count, total_size_mb
    
    except Exception as e:
        print(f"    Error checking bucket: {str(e)}")
        return False, 0, 0


 
# HELPER FUNCTION: GET BUCKET TAGS
 
def get_bucket_tags(bucket_name):
    """
    Gets tags for an S3 bucket
    Returns dictionary of tags or empty dict if no tags
    """
    try:
        response = s3_client.get_bucket_tagging(Bucket=bucket_name)
        tags = {tag['Key']: tag['Value'] for tag in response.get('TagSet', [])}
        return tags
    
    except s3_client.exceptions.ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'NoSuchTagSet':
            # Bucket has no tags
            return {}
        else:
            print(f"     Error getting tags: {str(e)}")
            return {}


 
# HELPER FUNCTION: DELETE EMPTY BUCKET
 
def delete_empty_bucket(bucket_name):
    """
    Deletes an empty S3 bucket
    
    Returns: True if successful, False otherwise
    """
    try:
        print(f"    Deleting empty bucket: {bucket_name}")
        
        s3_client.delete_bucket(Bucket=bucket_name)
        
        print(f"    Bucket {bucket_name} deleted successfully")
        
        return True
    
    except Exception as e:
        print(f"    Error deleting bucket: {str(e)}")
        return False


 
# HELPER FUNCTION: APPLY LIFECYCLE POLICY
 
def apply_lifecycle_policy(bucket_name):
    """
    Applies the 3-tier lifecycle policy to a bucket
    Standard (0-30d) → Standard-IA (30-180d) → Glacier Instant (180d+)
    """
    try:
        print(f"  📋 Applying lifecycle policy to {bucket_name}")
        
        lifecycle_config = {
            'Rules': [
                {
                    'ID': 'CostGuardian-3Tier-Auto',
                    'Status': 'Enabled',
                    'Filter': {'Prefix': ''},
                    'Transitions': [
                        {
                            'Days': 30,
                            'StorageClass': 'STANDARD_IA'
                        },
                        {
                            'Days': 180,
                            'StorageClass': 'GLACIER_IR'
                        }
                    ]
                }
            ]
        }
        
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_config
        )
        
        print(f"    Lifecycle policy applied successfully")
        print(f"     → 30 days: Standard → Standard-IA")
        print(f"     → 180 days: Standard-IA → Glacier Instant Retrieval")
        
        return True
    
    except Exception as e:
        print(f"    Error applying lifecycle policy: {str(e)}")
        return False


 
# HELPER FUNCTION: SEND S3 ALERT
 
def send_s3_bucket_alert(bucket_name, action, object_count=0, size_mb=0):
    """
    Sends email notification about S3 bucket actions
    action: 'EMPTY_WARNING', 'DELETED', or 'LIFECYCLE_APPLIED'
    """
    try:
        if action == 'EMPTY_WARNING':
            subject = f"  CostGuardian Alert - Empty S3 Bucket: {bucket_name}"
            
            message = f"""
CostGuardian has detected an EMPTY S3 bucket:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BUCKET DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bucket Name:      {bucket_name}
Object Count:     0
Total Size:       0 MB

   This bucket is empty and costing money!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Empty buckets don't cost for storage, but cost for:
- Lifecycle policies: $0.10-0.50/month
- Request charges (if any API calls)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CostGuardian will check again in 24 hours.

If still empty after 3 consecutive checks (3 days):
→ Bucket will be DELETED

To prevent deletion:
1. Add objects to the bucket, OR
2. Add tag: CostGuardianBucket=Protected

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'DELETED':
            subject = f"🗑️ CostGuardian Alert - S3 Bucket DELETED: {bucket_name}"
            
            message = f"""
CostGuardian has DELETED an empty S3 bucket:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BUCKET DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bucket Name:      {bucket_name}
Status:           DELETED 

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REASON
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bucket was empty for 3+ consecutive checks (3 days).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TO RECREATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If you need this bucket:
1. S3 Console → Create bucket
2. Use same name: {bucket_name}
3. Configure settings as needed

   Note: Bucket name may not be immediately available if recently deleted.

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'LIFECYCLE_APPLIED':
            subject = f"📋 CostGuardian - Lifecycle Policy Applied: {bucket_name}"
            
            message = f"""
CostGuardian has applied cost optimization lifecycle policy:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BUCKET DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Bucket Name:      {bucket_name}
Objects:          {object_count:,}
Total Size:       {size_mb:.2f} MB

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LIFECYCLE POLICY (3-TIER)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Day 0-30:   S3 Standard ($0.023/GB/month)
  Day 30-180: S3 Standard-IA ($0.0125/GB/month) - 46% cheaper
  Day 180+:   S3 Glacier Instant ($0.004/GB/month) - 83% cheaper

All retrievals remain INSTANT (milliseconds).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ESTIMATED SAVINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current cost (all Standard): ${size_mb * 0.023:.2f}/month
After 6 months (optimal):    ${size_mb * 0.004:.2f}/month

Monthly savings (long-term): ${size_mb * 0.019:.2f} (83% reduction)
Annual savings:              ${size_mb * 0.019 * 12:.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT HAPPENS NOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Objects older than 30 days will transition to Standard-IA
- Objects older than 180 days will transition to Glacier Instant
- Retrieval speed remains instant (no delays)
- No action required from you

To disable lifecycle:
S3 Console → {bucket_name} → Management → Delete lifecycle rule

-- CostGuardian Cost Optimization System
"""
        
        else:
            return
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    S3 bucket alert email sent!")
    
    except Exception as e:
        print(f"    Failed to send S3 alert: {str(e)}")

 
# HELPER FUNCTION: GET ALL EBS VOLUMES
 
def get_all_ebs_volumes():
    """
    Retrieves all EBS volumes in the account/region
    Returns list of volume details
    """
    try:
        print(f"\n  Scanning for EBS Volumes...")
        
        response = ec2_client.describe_volumes()
        
        volumes = response.get('Volumes', [])
        
        print(f"  Found {len(volumes)} EBS volume(s)")
        
        return volumes
    
    except Exception as e:
        print(f"  Error getting EBS volumes: {str(e)}")
        return []


 
# HELPER FUNCTION: GET VOLUME COST
 
def get_ebs_volume_cost(volume_type, size_gb, iops=None):
    """
    Estimates monthly cost for EBS volume based on type and size
    
    volume_type: gp2, gp3, io1, io2, st1, sc1, standard
    size_gb: volume size in GB
    iops: provisioned IOPS (for io1/io2 only)
    """
    
    # Pricing per GB/month (us-east-1)
    pricing = {
        'gp3': 0.08,      # General Purpose SSD (newest)
        'gp2': 0.10,      # General Purpose SSD (legacy)
        'io1': 0.125,     # Provisioned IOPS SSD
        'io2': 0.125,     # Provisioned IOPS SSD (newer)
        'st1': 0.045,     # Throughput Optimized HDD
        'sc1': 0.015,     # Cold HDD
        'standard': 0.05  # Magnetic (legacy)
    }
    
    price_per_gb = pricing.get(volume_type, 0.10)  # Default to gp2 if unknown
    
    base_cost = size_gb * price_per_gb
    
    # Add IOPS cost for io1/io2 volumes
    if volume_type in ['io1', 'io2'] and iops:
        iops_cost = iops * 0.065  # $0.065/IOPS/month
        return base_cost + iops_cost
    
    return base_cost


 
# HELPER FUNCTION: BACKUP EBS VOLUME CONFIG
 
def backup_ebs_volume_config(volume):
    """
    Backs up EBS volume configuration to S3
    Includes: volume details, attachments, snapshots
    """
    try:
        volume_id = volume['VolumeId']
        
        print(f"    Backing up EBS volume configuration...")
        
        # Get volume tags
        tags = {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}
        volume_name = tags.get('Name', 'Unnamed')
        
        # Get all snapshots for this volume
        snapshots_response = ec2_client.describe_snapshots(
            Filters=[
                {
                    'Name': 'volume-id',
                    'Values': [volume_id]
                }
            ],
            OwnerIds=['self']
        )
        
        snapshots = snapshots_response.get('Snapshots', [])
        
        config = {
            'VolumeId': volume_id,
            'VolumeName': volume_name,
            'Size': volume.get('Size'),
            'VolumeType': volume.get('VolumeType'),
            'State': volume.get('State'),
            'Iops': volume.get('Iops'),
            'Throughput': volume.get('Throughput'),
            'SnapshotId': volume.get('SnapshotId'),
            'AvailabilityZone': volume.get('AvailabilityZone'),
            'Encrypted': volume.get('Encrypted'),
            'KmsKeyId': volume.get('KmsKeyId'),
            'CreateTime': str(volume.get('CreateTime')),
            'Attachments': volume.get('Attachments', []),
            'Tags': volume.get('Tags', []),
            'MultiAttachEnabled': volume.get('MultiAttachEnabled'),
            'ExistingSnapshots': [
                {
                    'SnapshotId': snap['SnapshotId'],
                    'StartTime': str(snap['StartTime']),
                    'Progress': snap.get('Progress'),
                    'Description': snap.get('Description')
                }
                for snap in snapshots
            ],
            'EstimatedMonthlyCost': get_ebs_volume_cost(
                volume.get('VolumeType'),
                volume.get('Size'),
                volume.get('Iops')
            ),
            'BackupTimestamp': datetime.now().isoformat()
        }
        
        # Save to S3
        config_json = json.dumps(config, indent=2, default=str)
        
        s3_key = f'ebs-volumes/{volume_id}/config-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=config_json,
            ContentType='application/json'
        )
        
        print(f"    EBS volume configuration backed up to S3")
        print(f"  📁 s3://{S3_BUCKET}/{s3_key}")
        
        return True
    
    except Exception as e:
        print(f"    Backup failed: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


 
# HELPER FUNCTION: CREATE EBS SNAPSHOT
 
def create_ebs_snapshot(volume_id, volume_name):
    """
    Creates a snapshot of the EBS volume before deletion
    
    Returns: snapshot_id if successful, None otherwise
    """
    try:
        snapshot_description = f"CostGuardian final snapshot - {volume_name} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        print(f"    Creating EBS snapshot: {volume_id}")
        
        response = ec2_client.create_snapshot(
            VolumeId=volume_id,
            Description=snapshot_description,
            TagSpecifications=[
                {
                    'ResourceType': 'snapshot',
                    'Tags': [
                        {
                            'Key': 'CostGuardian',
                            'Value': 'AutoSnapshot'
                        },
                        {
                            'Key': 'OriginalVolume',
                            'Value': volume_id
                        },
                        {
                            'Key': 'OriginalVolumeName',
                            'Value': volume_name
                        },
                        {
                            'Key': 'CreatedDate',
                            'Value': datetime.now().isoformat()
                        }
                    ]
                }
            ]
        )
        
        snapshot_id = response['SnapshotId']
        
        print(f"    Snapshot creation initiated: {snapshot_id}")
        print(f"    Snapshot will complete in background (5-30 minutes depending on size)")
        
        return snapshot_id
    
    except Exception as e:
        print(f"    Snapshot creation failed: {str(e)}")
        return None


 
# HELPER FUNCTION: DELETE EBS VOLUME
 
def delete_ebs_volume(volume_id):
    """
    Deletes an EBS volume
    Should only be called after snapshot is created
    
    Returns: True if successful, False otherwise
    """
    try:
        print(f"    Deleting EBS volume: {volume_id}")
        
        ec2_client.delete_volume(VolumeId=volume_id)
        
        print(f"    EBS volume deleted successfully")
        
        return True
    
    except Exception as e:
        error_message = str(e)
        
        if 'InvalidVolume.NotFound' in error_message:
            print(f"  ℹ️  Volume already deleted")
            return True
        elif 'VolumeInUse' in error_message:
            print(f"    Volume is in use - cannot delete")
            return False
        else:
            print(f"    Error deleting volume: {error_message}")
            return False


 
# HELPER FUNCTION: SEND EBS ALERT
 
def send_ebs_volume_alert(volume, action, snapshot_id=None):
    """
    Sends email notification about EBS volume actions
    action: 'UNATTACHED_WARNING' or 'DELETED'
    """
    try:
        volume_id = volume['VolumeId']
        size_gb = volume.get('Size')
        volume_type = volume.get('VolumeType')
        availability_zone = volume.get('AvailabilityZone')
        
        # Get name from tags
        tags = {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}
        volume_name = tags.get('Name', 'Unnamed')
        
        # Calculate cost
        estimated_cost = get_ebs_volume_cost(volume_type, size_gb, volume.get('Iops'))
        
        # Check if volume has attachments history
        attachments = volume.get('Attachments', [])
        last_instance = None
        if attachments:
            last_instance = attachments[0].get('InstanceId', 'Unknown')
        
        if action == 'UNATTACHED_WARNING':
            subject = f"  CostGuardian Alert - Unattached EBS Volume: {volume_name}"
            
            message = f"""
CostGuardian has detected an UNATTACHED EBS volume:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VOLUME DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Volume ID:        {volume_id}
Name:             {volume_name}
Size:             {size_gb} GB
Type:             {volume_type}
Zone:             {availability_zone}
State:            {volume.get('State')}  
Encrypted:        {volume.get('Encrypted')}
Created:          {volume.get('CreateTime')}
Last Instance:    {last_instance if last_instance else 'Never attached'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current Monthly Cost:  ${estimated_cost:.2f}
Annual Cost:           ${estimated_cost * 12:.2f}

  This volume is not attached to any instance but still costing money!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS TAKEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Configuration backed up to S3

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CostGuardian will check again in 24 hours.

If still unattached after 3 consecutive checks (3 days):
→ Snapshot will be created
→ Volume will be DELETED
→ You can restore from snapshot if needed

To prevent deletion:
1. Attach volume to an EC2 instance, OR
2. Add tag: CostGuardian=Ignore

Backups: s3://{S3_BUCKET}/ebs-volumes/{volume_id}/

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'DELETED':
            subject = f"🗑️ CostGuardian Alert - EBS Volume DELETED: {volume_name}"
            
            message = f"""
CostGuardian has DELETED an unattached EBS volume:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VOLUME DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Volume ID:        {volume_id}
Name:             {volume_name}
Size:             {size_gb} GB
Type:             {volume_type}
Status:           DELETED 

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST SAVINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Monthly Savings:  ${estimated_cost:.2f}
  Annual Savings:   ${estimated_cost * 12:.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR BACKUPS (PRESERVED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 S3 Configuration:
   s3://{S3_BUCKET}/ebs-volumes/{volume_id}/
   
   Contains: Complete volume configuration and metadata

📁 EBS Snapshot:
   Snapshot ID: {snapshot_id}
   Location: EC2 Console → Snapshots
   Size: {size_gb} GB
   Cost: ~${size_gb * 0.05:.2f}/month (snapshot storage)
   
      Snapshot will remain until you manually delete it

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TO RESTORE THIS VOLUME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. EC2 Console → Snapshots
2. Find snapshot: {snapshot_id}
3. Actions → Create volume from snapshot
4. Configure settings from S3 backup
5. Attach to EC2 instance

Restore time: 5-10 minutes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SNAPSHOT CLEANUP REMINDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The snapshot will cost ${size_gb * 0.05:.2f}/month.

If you don't need this data, delete the snapshot:
EC2 Console → Snapshots → {snapshot_id} → Delete

-- CostGuardian Cost Optimization System
"""
        
        else:
            return
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    EBS volume alert email sent!")
    
    except Exception as e:
        print(f"    Failed to send EBS alert: {str(e)}")
        
 
# HELPER FUNCTION: GET ALL LOAD BALANCERS
 
def get_all_load_balancers():
    """
    Retrieves all Application and Network Load Balancers (ALB/NLB)
    Returns list of load balancer details
    """
    try:
        print(f"\n🔄 Scanning for Application/Network Load Balancers...")
        
        response = elbv2_client.describe_load_balancers()
        
        load_balancers = response.get('LoadBalancers', [])
        
        print(f"  Found {len(load_balancers)} ALB/NLB load balancer(s)")
        
        return load_balancers
    
    except Exception as e:
        print(f"  Error getting load balancers: {str(e)}")
        return []


 
# HELPER FUNCTION: GET CLASSIC LOAD BALANCERS
 
def get_all_classic_load_balancers():
    """
    Retrieves all Classic Load Balancers (CLB)
    Returns list of CLB details
    """
    try:
        print(f"\n🔄 Scanning for Classic Load Balancers...")
        
        response = elb_client.describe_load_balancers()
        
        load_balancers = response.get('LoadBalancerDescriptions', [])
        
        print(f"  Found {len(load_balancers)} Classic load balancer(s)")
        
        return load_balancers
    
    except Exception as e:
        print(f"  Error getting classic load balancers: {str(e)}")
        return []


 
# HELPER FUNCTION: CHECK ALB/NLB USAGE
 
def check_load_balancer_usage(lb_arn, lb_name, lb_type):
    """
    Checks CloudWatch metrics to determine if ALB/NLB is being used
    
    Metrics checked:
    - HealthyHostCount (target health)
    - ActiveConnectionCount (active connections)
    - ProcessedBytes (data transferred)
    - RequestCount (for ALB only)
    
    Returns: (healthy_targets, connections, bytes_processed, is_idle)
    """
    try:
        print(f"    Checking usage metrics for {lb_name}...")
        
        # Extract load balancer namespace from ARN
        # ARN format: arn:aws:elasticloadbalancing:region:account:loadbalancer/app/name/id
        lb_full_name = lb_arn.split(':loadbalancer/')[1]
        
        # Check last 7 days
        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
        
        # Metric 1: Healthy Host Count
        healthy_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/ApplicationELB' if lb_type in ['application', 'alb'] else 'AWS/NetworkELB',
            MetricName='HealthyHostCount',
            Dimensions=[
                {
                    'Name': 'LoadBalancer',
                    'Value': lb_full_name
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,  # 1 day
            Statistics=['Average', 'Maximum']
        )
        
        # Metric 2: Active Connection Count
        connections_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/ApplicationELB' if lb_type in ['application', 'alb'] else 'AWS/NetworkELB',
            MetricName='ActiveConnectionCount',
            Dimensions=[
                {
                    'Name': 'LoadBalancer',
                    'Value': lb_full_name
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=['Sum']
        )
        
        # Metric 3: Processed Bytes
        bytes_response = cloudwatch.get_metric_statistics(
            Namespace='AWS/ApplicationELB' if lb_type in ['application', 'alb'] else 'AWS/NetworkELB',
            MetricName='ProcessedBytes',
            Dimensions=[
                {
                    'Name': 'LoadBalancer',
                    'Value': lb_full_name
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=['Sum']
        )
        
        # Calculate metrics
        healthy_datapoints = healthy_response.get('Datapoints', [])
        connection_datapoints = connections_response.get('Datapoints', [])
        bytes_datapoints = bytes_response.get('Datapoints', [])
        
        # Average healthy targets
        if healthy_datapoints:
            avg_healthy = sum(dp['Average'] for dp in healthy_datapoints) / len(healthy_datapoints)
            max_healthy = max(dp['Maximum'] for dp in healthy_datapoints)
        else:
            avg_healthy = 0
            max_healthy = 0
        
        # Total connections
        total_connections = sum(dp['Sum'] for dp in connection_datapoints)
        
        # Total bytes processed
        total_bytes = sum(dp['Sum'] for dp in bytes_datapoints)
        total_mb = total_bytes / 1024 / 1024
        
        print(f"    Healthy Targets (avg): {avg_healthy:.1f}")
        print(f"    Max Healthy Targets: {max_healthy:.0f}")
        print(f"    Total Connections (7d): {total_connections:.0f}")
        print(f"    Processed Data (7d): {total_mb:.2f} MB")
        
        # Determine if idle
        is_idle = (
            max_healthy == 0 and  # No healthy targets
            total_connections < LB_MIN_CONNECTIONS_THRESHOLD and
            total_bytes < LB_MIN_BYTES_THRESHOLD
        )
        
        if is_idle:
            print(f"     Load Balancer appears IDLE (0 targets, minimal traffic)")
        else:
            print(f"    Load Balancer is ACTIVE")
        
        return avg_healthy, total_connections, total_bytes, is_idle
    
    except Exception as e:
        print(f"    Error checking metrics: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        # If can't get metrics, assume active (safer)
        return 0, 0, 0, False


 
# HELPER FUNCTION: BACKUP LOAD BALANCER CONFIG
 
def backup_load_balancer_config(lb, lb_type='application'):
    """
    Backs up complete load balancer configuration to S3
    Includes: listeners, target groups, rules, attributes
    """
    try:
        lb_arn = lb['LoadBalancerArn']
        lb_name = lb['LoadBalancerName']
        
        print(f"    Backing up load balancer configuration...")
        
        # Basic LB details
        config = {
            'LoadBalancerArn': lb_arn,
            'LoadBalancerName': lb_name,
            'DNSName': lb.get('DNSName'),
            'CanonicalHostedZoneId': lb.get('CanonicalHostedZoneId'),
            'CreatedTime': str(lb.get('CreatedTime')),
            'LoadBalancerType': lb_type,
            'Scheme': lb.get('Scheme'),
            'VpcId': lb.get('VpcId'),
            'State': lb.get('State'),
            'Type': lb.get('Type'),
            'IpAddressType': lb.get('IpAddressType'),
            'SecurityGroups': lb.get('SecurityGroups', []),
            'AvailabilityZones': lb.get('AvailabilityZones', []),
            'BackupTimestamp': datetime.now().isoformat()
        }
        
        # Get listeners
        try:
            listeners_response = elbv2_client.describe_listeners(LoadBalancerArn=lb_arn)
            config['Listeners'] = listeners_response.get('Listeners', [])
            print(f"      Captured {len(config['Listeners'])} listener(s)")
        except Exception as e:
            print(f"       Could not fetch listeners: {str(e)}")
            config['Listeners'] = []
        
        # Get target groups
        try:
            target_groups_response = elbv2_client.describe_target_groups(LoadBalancerArn=lb_arn)
            config['TargetGroups'] = []
            
            for tg in target_groups_response.get('TargetGroups', []):
                tg_arn = tg['TargetGroupArn']
                
                # Get target health
                health_response = elbv2_client.describe_target_health(TargetGroupArn=tg_arn)
                tg['TargetHealth'] = health_response.get('TargetHealthDescriptions', [])
                
                config['TargetGroups'].append(tg)
            
            print(f"      Captured {len(config['TargetGroups'])} target group(s)")
        except Exception as e:
            print(f"       Could not fetch target groups: {str(e)}")
            config['TargetGroups'] = []
        
        # Get load balancer attributes
        try:
            attributes_response = elbv2_client.describe_load_balancer_attributes(LoadBalancerArn=lb_arn)
            config['Attributes'] = attributes_response.get('Attributes', [])
        except Exception as e:
            print(f"       Could not fetch attributes: {str(e)}")
            config['Attributes'] = []
        
        # Get tags
        try:
            tags_response = elbv2_client.describe_tags(ResourceArns=[lb_arn])
            if tags_response.get('TagDescriptions'):
                config['Tags'] = tags_response['TagDescriptions'][0].get('Tags', [])
        except Exception as e:
            print(f"       Could not fetch tags: {str(e)}")
            config['Tags'] = []
        
        # Calculate cost
        if lb_type in ['application', 'alb']:
            config['EstimatedMonthlyCost'] = 16.20
        elif lb_type in ['network', 'nlb']:
            config['EstimatedMonthlyCost'] = 16.20
        else:
            config['EstimatedMonthlyCost'] = 18.00  # Classic LB
        
        # Save to S3
        config_json = json.dumps(config, indent=2, default=str)
        
        s3_key = f'load-balancers/{lb_name}/config-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=config_json,
            ContentType='application/json'
        )
        
        print(f"    Load balancer configuration backed up to S3")
        print(f"  📁 s3://{S3_BUCKET}/{s3_key}")
        
        return True
    
    except Exception as e:
        print(f"    Backup failed: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


 
# HELPER FUNCTION: DELETE LOAD BALANCER
 
def delete_load_balancer(lb_arn, lb_name):
    """
    Deletes an Application or Network Load Balancer
    
    Returns: True if successful, False otherwise
    """
    try:
        print(f"    Deleting load balancer: {lb_name}")
        
        elbv2_client.delete_load_balancer(LoadBalancerArn=lb_arn)
        
        print(f"    Load balancer deletion initiated")
        print(f"    Deletion will complete in 2-5 minutes")
        
        return True
    
    except Exception as e:
        print(f"    Error deleting load balancer: {str(e)}")
        return False


 
# HELPER FUNCTION: SEND LOAD BALANCER ALERT
 
def send_load_balancer_alert(lb, action, healthy_targets, connections, bytes_processed):
    """
    Sends email notification about load balancer actions
    action: 'IDLE_WARNING' or 'DELETED'
    """
    try:
        lb_name = lb['LoadBalancerName']
        lb_type = lb.get('Type', 'application')
        dns_name = lb.get('DNSName')
        
        # Get tags
        tags = {}
        if 'Tags' in lb:
            tags = {tag['Key']: tag['Value'] for tag in lb.get('Tags', [])}
        
        # Calculate cost
        monthly_cost = 16.20 if lb_type in ['application', 'network'] else 18.00
        
        mb_processed = bytes_processed / 1024 / 1024
        
        if action == 'IDLE_WARNING':
            subject = f"  CostGuardian Alert - Idle Load Balancer: {lb_name}"
            
            message = f"""
CostGuardian has detected an IDLE load balancer:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOAD BALANCER DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name:             {lb_name}
Type:             {lb_type.upper()}
DNS:              {dns_name}
State:            {lb.get('State', {}).get('Code', 'unknown')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE (LAST 7 DAYS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Healthy Targets:  {healthy_targets:.0f}
Connections:      {connections:.0f}
Data Processed:   {mb_processed:.2f} MB

   This load balancer has NO healthy targets and minimal traffic!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current Monthly Cost:  ${monthly_cost:.2f}
Annual Cost:           ${monthly_cost * 12:.2f}

  If deleted, you'll save ${monthly_cost:.2f}/month!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CostGuardian will check again in 1 hour.

If still idle after 3 consecutive checks (3 hours):
→ Load balancer will be DELETED
→ Configuration will be preserved in S3

To prevent deletion:
1. Register healthy targets to the load balancer, OR
2. Add tag: CostGuardian=Ignore

Backups: s3://{S3_BUCKET}/load-balancers/{lb_name}/

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'DELETED':
            subject = f"🗑️ CostGuardian Alert - Load Balancer DELETED: {lb_name}"
            
            message = f"""
CostGuardian has DELETED an idle load balancer:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOAD BALANCER DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name:             {lb_name}
Type:             {lb_type.upper()}
Previous DNS:     {dns_name}
Status:           DELETED 

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST SAVINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Monthly Savings:  ${monthly_cost:.2f}
  Annual Savings:   ${monthly_cost * 12:.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR BACKUPS (PRESERVED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 S3 Configuration:
   s3://{S3_BUCKET}/load-balancers/{lb_name}/
   
   Contains:
   • Complete load balancer configuration
   • Listener rules
   • Target group settings
   • Security group associations
   • Attributes and tags

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TO RECREATE THIS LOAD BALANCER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. EC2 Console → Load Balancers
2. Create Load Balancer
3. Use configuration from S3 backup
4. Recreate listeners and target groups
5. Update DNS records to new DNS name

   Note: You'll get a new DNS name (old one is gone)

-- CostGuardian Cost Optimization System
"""
        
        else:
            return
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    Load balancer alert email sent!")
    
    except Exception as e:
        print(f"    Failed to send load balancer alert: {str(e)}")        

 
# HELPER FUNCTION: GET ALL VPCS
 
def get_all_vpcs():
    """
    Retrieves all VPCs in the region
    Returns list of VPC details
    """
    try:
        print(f"\n🌐 Scanning for VPCs...")
        
        response = ec2_client.describe_vpcs()
        
        vpcs = response.get('Vpcs', [])
        
        print(f"  Found {len(vpcs)} VPC(s)")
        
        return vpcs
    
    except Exception as e:
        print(f"  Error getting VPCs: {str(e)}")
        return []


 
# HELPER FUNCTION: CHECK IF VPC IS EMPTY
 
def is_vpc_empty(vpc_id):
    """
    Checks if a VPC has any resources using it
    
    Returns: (is_empty, resource_count, resource_summary)
    """
    try:
        print(f"    Checking resources in VPC {vpc_id}...")
        
        resource_summary = {
            'ec2_instances': 0,
            'rds_instances': 0,
            'load_balancers': 0,
            'nat_gateways': 0,
            'vpc_endpoints': 0,
            'lambda_functions': 0,
            'ecs_tasks': 0,
            'elasticache_clusters': 0
        }
        
        # Check 1: EC2 Instances
        try:
            ec2_response = ec2_client.describe_instances(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'instance-state-name', 'Values': ['running', 'stopped', 'stopping', 'pending']}
                ]
            )
            
            for reservation in ec2_response['Reservations']:
                resource_summary['ec2_instances'] += len(reservation['Instances'])
        except Exception as e:
            print(f"       Error checking EC2: {str(e)}")
        
        # Check 2: RDS Instances
        try:
            rds_client_temp = boto3.client('rds', region_name=AWS_REGION)
            rds_response = rds_client_temp.describe_db_instances()
            
            for db in rds_response.get('DBInstances', []):
                if db.get('DBSubnetGroup', {}).get('VpcId') == vpc_id:
                    resource_summary['rds_instances'] += 1
        except Exception as e:
            print(f"       Error checking RDS: {str(e)}")
        
        # Check 3: Load Balancers (ALB/NLB)
        try:
            lb_response = elbv2_client.describe_load_balancers()
            
            for lb in lb_response.get('LoadBalancers', []):
                if lb.get('VpcId') == vpc_id:
                    resource_summary['load_balancers'] += 1
        except Exception as e:
            print(f"       Error checking Load Balancers: {str(e)}")
        
        # Check 4: NAT Gateways
        try:
            nat_response = ec2_client.describe_nat_gateways(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'state', 'Values': ['available', 'pending']}
                ]
            )
            
            resource_summary['nat_gateways'] = len(nat_response.get('NatGateways', []))
        except Exception as e:
            print(f"       Error checking NAT Gateways: {str(e)}")
        
        # Check 5: VPC Endpoints (Interface type - these cost money)
        try:
            endpoint_response = ec2_client.describe_vpc_endpoints(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'vpc-endpoint-type', 'Values': ['Interface']}
                ]
            )
            
            resource_summary['vpc_endpoints'] = len(endpoint_response.get('VpcEndpoints', []))
        except Exception as e:
            print(f"       Error checking VPC Endpoints: {str(e)}")
        
        # Calculate totals
        total_resources = sum(resource_summary.values())
        
        # Print summary
        print(f"      Resource Count:")
        print(f"       EC2 Instances: {resource_summary['ec2_instances']}")
        print(f"       RDS Instances: {resource_summary['rds_instances']}")
        print(f"       Load Balancers: {resource_summary['load_balancers']}")
        print(f"       NAT Gateways: {resource_summary['nat_gateways']}")
        print(f"       VPC Endpoints: {resource_summary['vpc_endpoints']}")
        print(f"       Total: {total_resources}")
        
        is_empty = (total_resources == 0)
        
        if is_empty:
            print(f"    VPC is EMPTY (no resources)")
        else:
            print(f"  ℹ️  VPC has {total_resources} resource(s)")
        
        return is_empty, total_resources, resource_summary
    
    except Exception as e:
        print(f"    Error checking VPC resources: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        # If error checking, assume NOT empty (safer)
        return False, -1, {}


 
# HELPER FUNCTION: GET ORPHANED SUBNETS
 
def get_orphaned_subnets(vpc_id):
    """
    Gets all subnets in a VPC and checks which are orphaned
    
    Returns: list of orphaned subnet IDs
    """
    try:
        print(f"    Checking subnets in VPC {vpc_id}...")
        
        # Get all subnets in VPC
        subnet_response = ec2_client.describe_subnets(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        
        subnets = subnet_response.get('Subnets', [])
        orphaned_subnets = []
        
        for subnet in subnets:
            subnet_id = subnet['SubnetId']
            
            # Check if subnet has any network interfaces (resources using it)
            ni_response = ec2_client.describe_network_interfaces(
                Filters=[{'Name': 'subnet-id', 'Values': [subnet_id]}]
            )
            
            network_interfaces = ni_response.get('NetworkInterfaces', [])
            
            if len(network_interfaces) == 0:
                orphaned_subnets.append(subnet)
                print(f"       Orphaned subnet: {subnet_id} ({subnet.get('CidrBlock')})")
            else:
                print(f"      Active subnet: {subnet_id} ({len(network_interfaces)} network interfaces)")
        
        return orphaned_subnets
    
    except Exception as e:
        print(f"    Error checking subnets: {str(e)}")
        return []


 
# HELPER FUNCTION: BACKUP VPC CONFIG
 
def backup_vpc_config(vpc):
    """
    Backs up complete VPC configuration to S3
    Includes: VPC, subnets, route tables, internet gateways, security groups
    """
    try:
        vpc_id = vpc['VpcId']
        
        print(f"    Backing up VPC configuration...")
        
        # Basic VPC details
        config = {
            'VpcId': vpc_id,
            'CidrBlock': vpc.get('CidrBlock'),
            'CidrBlockAssociationSet': vpc.get('CidrBlockAssociationSet', []),
            'DhcpOptionsId': vpc.get('DhcpOptionsId'),
            'State': vpc.get('State'),
            'IsDefault': vpc.get('IsDefault'),
            'Tags': vpc.get('Tags', []),
            'BackupTimestamp': datetime.now().isoformat()
        }
        
        # Get VPC name from tags
        vpc_name = "Unnamed"
        for tag in vpc.get('Tags', []):
            if tag['Key'] == 'Name':
                vpc_name = tag['Value']
                break
        
        config['VpcName'] = vpc_name
        
        # Get Subnets
        try:
            subnet_response = ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            config['Subnets'] = subnet_response.get('Subnets', [])
            print(f"      Captured {len(config['Subnets'])} subnet(s)")
        except Exception as e:
            print(f"       Could not fetch subnets: {str(e)}")
            config['Subnets'] = []
        
        # Get Route Tables
        try:
            rt_response = ec2_client.describe_route_tables(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            config['RouteTables'] = rt_response.get('RouteTables', [])
            print(f"      Captured {len(config['RouteTables'])} route table(s)")
        except Exception as e:
            print(f"       Could not fetch route tables: {str(e)}")
            config['RouteTables'] = []
        
        # Get Internet Gateways
        try:
            igw_response = ec2_client.describe_internet_gateways(
                Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
            )
            config['InternetGateways'] = igw_response.get('InternetGateways', [])
            print(f"      Captured {len(config['InternetGateways'])} internet gateway(s)")
        except Exception as e:
            print(f"       Could not fetch internet gateways: {str(e)}")
            config['InternetGateways'] = []
        
        # Get Security Groups
        try:
            sg_response = ec2_client.describe_security_groups(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            config['SecurityGroups'] = sg_response.get('SecurityGroups', [])
            print(f"      Captured {len(config['SecurityGroups'])} security group(s)")
        except Exception as e:
            print(f"       Could not fetch security groups: {str(e)}")
            config['SecurityGroups'] = []
        
        # Get Network ACLs
        try:
            nacl_response = ec2_client.describe_network_acls(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            config['NetworkAcls'] = nacl_response.get('NetworkAcls', [])
            print(f"      Captured {len(config['NetworkAcls'])} network ACL(s)")
        except Exception as e:
            print(f"       Could not fetch network ACLs: {str(e)}")
            config['NetworkAcls'] = []
        
        # Save to S3
        config_json = json.dumps(config, indent=2, default=str)
        
        s3_key = f'vpc-configs/{vpc_id}/full-backup-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=config_json,
            ContentType='application/json'
        )
        
        print(f"    VPC configuration backed up to S3")
        print(f"  📁 s3://{S3_BUCKET}/{s3_key}")
        
        return True
    
    except Exception as e:
        print(f"    Backup failed: {str(e)}")
        import traceback
        print(f"    Traceback: {traceback.format_exc()}")
        return False


 
# HELPER FUNCTION: DELETE ORPHANED SUBNETS
 
def delete_orphaned_subnets(vpc_id):
    """
    Deletes all orphaned subnets in a VPC
    Must be done before deleting the VPC itself
    
    Returns: (success, deleted_subnet_ids)
    """
    try:
        print(f"  🧹 Cleaning up orphaned subnets...")
        
        orphaned_subnets = get_orphaned_subnets(vpc_id)
        deleted_subnets = []
        
        if len(orphaned_subnets) == 0:
            print(f"  ℹ️  No orphaned subnets to delete")
            return True, []
        
        for subnet in orphaned_subnets:
            subnet_id = subnet['SubnetId']
            
            try:
                print(f"      Deleting subnet: {subnet_id}")
                
                ec2_client.delete_subnet(SubnetId=subnet_id)
                
                deleted_subnets.append(subnet_id)
                print(f"      Subnet deleted: {subnet_id}")
            
            except Exception as e:
                print(f"      Failed to delete subnet {subnet_id}: {str(e)}")
        
        if len(deleted_subnets) > 0:
            print(f"    Deleted {len(deleted_subnets)} orphaned subnet(s)")
        
        return True, deleted_subnets
    
    except Exception as e:
        print(f"    Error deleting subnets: {str(e)}")
        return False, []


 
# HELPER FUNCTION: DELETE VPC DEPENDENCIES
 
def delete_vpc_dependencies(vpc_id):
    """
    Deletes VPC dependencies that must be removed before VPC deletion:
    - Internet Gateways (detach & delete)
    - Non-default Security Groups (delete)
    - Non-default Network ACLs (delete)
    - Non-main Route Tables (delete)
    
    Returns: (success, summary)
    """
    try:
        print(f"  🧹 Cleaning up VPC dependencies...")
        
        summary = {
            'internet_gateways': 0,
            'security_groups': 0,
            'network_acls': 0,
            'route_tables': 0
        }
        
        # Step 1: Detach and delete Internet Gateways
        try:
            igw_response = ec2_client.describe_internet_gateways(
                Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
            )
            
            for igw in igw_response.get('InternetGateways', []):
                igw_id = igw['InternetGatewayId']
                
                print(f"    🔌 Detaching Internet Gateway: {igw_id}")
                
                # Detach from VPC
                ec2_client.detach_internet_gateway(
                    InternetGatewayId=igw_id,
                    VpcId=vpc_id
                )
                
                # Delete IGW
                ec2_client.delete_internet_gateway(InternetGatewayId=igw_id)
                
                summary['internet_gateways'] += 1
                print(f"      Internet Gateway deleted: {igw_id}")
        
        except Exception as e:
            print(f"       Error with Internet Gateways: {str(e)}")
        
        # Step 2: Delete non-default Security Groups
        try:
            sg_response = ec2_client.describe_security_groups(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for sg in sg_response.get('SecurityGroups', []):
                sg_id = sg['GroupId']
                sg_name = sg['GroupName']
                
                # Skip default security group
                if sg_name == 'default':
                    continue
                
                try:
                    print(f"      Deleting Security Group: {sg_name} ({sg_id})")
                    
                    ec2_client.delete_security_group(GroupId=sg_id)
                    
                    summary['security_groups'] += 1
                    print(f"      Security Group deleted: {sg_id}")
                
                except Exception as e:
                    print(f"       Could not delete SG {sg_id}: {str(e)}")
        
        except Exception as e:
            print(f"       Error with Security Groups: {str(e)}")
        
        # Step 3: Delete non-main Route Tables
        try:
            rt_response = ec2_client.describe_route_tables(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for rt in rt_response.get('RouteTables', []):
                rt_id = rt['RouteTableId']
                
                # Check if it's the main route table
                is_main = any(
                    assoc.get('Main', False) 
                    for assoc in rt.get('Associations', [])
                )
                
                if is_main:
                    print(f"       Skipping main route table: {rt_id}")
                    continue
                
                try:
                    print(f"      Deleting Route Table: {rt_id}")
                    
                    ec2_client.delete_route_table(RouteTableId=rt_id)
                    
                    summary['route_tables'] += 1
                    print(f"      Route Table deleted: {rt_id}")
                
                except Exception as e:
                    print(f"       Could not delete RT {rt_id}: {str(e)}")
        
        except Exception as e:
            print(f"       Error with Route Tables: {str(e)}")
        
        print(f"    Dependencies cleanup complete:")
        print(f"     Internet Gateways: {summary['internet_gateways']}")
        print(f"     Security Groups: {summary['security_groups']}")
        print(f"     Route Tables: {summary['route_tables']}")
        
        return True, summary
    
    except Exception as e:
        print(f"    Error cleaning dependencies: {str(e)}")
        return False, summary


 
# HELPER FUNCTION: DELETE VPC
 
def delete_vpc(vpc_id):
    """
    Deletes a VPC after all dependencies are removed
    
    Returns: True if successful, False otherwise
    """
    try:
        print(f"    Deleting VPC: {vpc_id}")
        
        # Step 1: Delete orphaned subnets
        subnet_success, deleted_subnets = delete_orphaned_subnets(vpc_id)
        
        if not subnet_success:
            print(f"    Failed to delete subnets - aborting VPC deletion")
            return False
        
        # Step 2: Delete VPC dependencies
        dep_success, dep_summary = delete_vpc_dependencies(vpc_id)
        
        if not dep_success:
            print(f"     Some dependencies could not be deleted")
        
        # Step 3: Delete the VPC itself
        print(f"    Deleting VPC: {vpc_id}")
        
        ec2_client.delete_vpc(VpcId=vpc_id)
        
        print(f"    VPC deleted successfully: {vpc_id}")
        
        return True
    
    except Exception as e:
        error_message = str(e)
        
        if 'DependencyViolation' in error_message:
            print(f"    VPC still has dependencies: {error_message}")
            print(f"  💡 Manual cleanup may be required")
        else:
            print(f"    Error deleting VPC: {error_message}")
        
        return False


 
# HELPER FUNCTION: SEND VPC ALERT
 
def send_vpc_alert(vpc, action, resource_count, resource_summary):
    """
    Sends email notification about VPC cleanup actions
    action: 'IDLE_WARNING' or 'DELETED'
    """
    try:
        vpc_id = vpc['VpcId']
        cidr_block = vpc.get('CidrBlock')
        
        # Get VPC name
        vpc_name = "Unnamed"
        for tag in vpc.get('Tags', []):
            if tag['Key'] == 'Name':
                vpc_name = tag['Value']
                break
        
        if action == 'IDLE_WARNING':
            subject = f"  CostGuardian Alert - Empty VPC Detected: {vpc_name}"
            
            message = f"""
CostGuardian has detected an EMPTY VPC with no resources:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VPC DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VPC ID:           {vpc_id}
Name:             {vpc_name}
CIDR Block:       {cidr_block}
Status:           EMPTY (0 resources)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESOURCE CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EC2 Instances:    {resource_summary.get('ec2_instances', 0)}
RDS Instances:    {resource_summary.get('rds_instances', 0)}
Load Balancers:   {resource_summary.get('load_balancers', 0)}
NAT Gateways:     {resource_summary.get('nat_gateways', 0)}
VPC Endpoints:    {resource_summary.get('vpc_endpoints', 0)}

   This VPC has no resources and may be leftover from testing!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COST IMPACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Empty VPCs are FREE, but they create clutter and
potential security confusion.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS TAKEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Complete VPC configuration backed up to S3

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CostGuardian will check again in 1 hour.

If still empty after 3 consecutive checks (3 hours):
→ VPC will be DELETED (subnets, IGWs, etc.)
→ Full configuration preserved in S3

To prevent deletion:
1. Add resources to the VPC, OR
2. Add tag: CostGuardian=Ignore

Backups: s3://{S3_BUCKET}/vpc-configs/{vpc_id}/

-- CostGuardian Cost Optimization System
"""
        
        elif action == 'DELETED':
            subject = f"🗑️ CostGuardian Alert - Empty VPC DELETED: {vpc_name}"
            
            message = f"""
CostGuardian has DELETED an empty VPC:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VPC DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VPC ID:           {vpc_id}
Name:             {vpc_name}
CIDR Block:       {cidr_block}
Status:           DELETED 

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLEANUP PERFORMED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Orphaned subnets deleted
  Internet Gateways detached and deleted
  Non-default Security Groups deleted
  Non-main Route Tables deleted
  VPC deleted

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR BACKUPS (PRESERVED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 S3 Configuration:
   s3://{S3_BUCKET}/vpc-configs/{vpc_id}/
   
   Contains:
   • Complete VPC configuration
   • All subnet details
   • Route tables
   • Internet Gateway settings
   • Security Group rules
   • Network ACLs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TO RECREATE THIS VPC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. VPC Console → Create VPC
2. Use CIDR: {cidr_block}
3. Recreate subnets from S3 backup
4. Recreate route tables and IGWs
5. Restore security group rules

Or use Infrastructure as Code (Terraform/CloudFormation)
from the S3 backup configuration.

-- CostGuardian Cost Optimization System
"""
        
        else:
            return
        
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        print(f"    VPC alert email sent!")
    
    except Exception as e:
        print(f"    Failed to send VPC alert: {str(e)}")



def validate_configuration():
   
    try:
        # Check S3 bucket exists
        s3_client.head_bucket(Bucket=S3_BUCKET)
        print(f"  S3 bucket validated: {S3_BUCKET}")
        
        # Check DynamoDB table exists
        dynamodb.Table(DYNAMODB_TABLE).table_status
        print(f"  DynamoDB table validated: {DYNAMODB_TABLE}")
        
        # Check SNS topic exists
        sns_client.get_topic_attributes(TopicArn=SNS_TOPIC_ARN)
        print(f"  SNS topic validated")
        
        return True
    except Exception as e:
        print(f"  Configuration error: {str(e)}")
        print("Please check:")
        print(f"  - S3 bucket: {S3_BUCKET}")
        print(f"  - DynamoDB table: {DYNAMODB_TABLE}")
        print(f"  - SNS topic ARN: {SNS_TOPIC_ARN}")
        raise
    
    
    
