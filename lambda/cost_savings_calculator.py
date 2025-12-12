"""
CostGuardian - Cost Savings Calculator Lambda
==============================================
Reads DynamoDB logs, calculates savings, generates dashboard data
"""

import json
import boto3
from datetime import datetime, timedelta
from decimal import Decimal
import os

# Import pricing data
from pricing import (
    calculate_monthly_savings,
    get_hourly_rate,
    format_currency,
    SERVICE_NAMES,
    HOURS_PER_MONTH
)

# AWS clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')

# Environment variables
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE')
S3_BUCKET = os.environ.get('S3_BUCKET_NAME')
AWS_REGION = os.environ.get('REGION', 'us-west-1')

# DynamoDB table
table = dynamodb.Table(DYNAMODB_TABLE)

class DecimalEncoder(json.JSONEncoder):
    """Helper to encode Decimal types to JSON"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def lambda_handler(event, context):
    """
    Main Lambda handler
    Calculates cost savings and generates dashboard data
    """
    print("🚀 CostGuardian Savings Calculator started...")
    
    try:
        # Get current month data
        current_month = datetime.now().strftime('%Y-%m')
        
        # Calculate savings
        savings_data = calculate_savings(current_month)
        
        # Generate dashboard JSON
        dashboard_data = generate_dashboard_data(savings_data)
        
        # Upload to S3
        upload_dashboard_data(dashboard_data)
        
        # Check if month-end for archiving
        if is_month_end():
            archive_monthly_report(savings_data)
        
        print("✅ Savings calculation complete!")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Success',
                'total_savings': savings_data['total_savings'],
                'resources_deleted': savings_data['total_resources']
            })
        }
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

def calculate_savings(month):
    """
    Calculate savings for a given month
    
    Args:
        month: YYYY-MM format (e.g., '2025-01')
    
    Returns:
        Dictionary with savings data
    """
    print(f"📊 Calculating savings for {month}...")
    
    # Get month boundaries
    month_start = datetime.strptime(f"{month}-01", '%Y-%m-%d')
    if month_start.month == 12:
        month_end = datetime(month_start.year + 1, 1, 1)
    else:
        month_end = datetime(month_start.year, month_start.month + 1, 1)
    
    month_start_ts = int(month_start.timestamp())
    month_end_ts = int(month_end.timestamp())
    
    # Query DynamoDB for deleted resources this month
    deleted_resources = []
    
    # Scan with filter (could optimize with GSI if needed)
    response = table.scan(
        FilterExpression='#status IN (:deleted, :stopped) AND #ts BETWEEN :start AND :end',
        ExpressionAttributeNames={
            '#status': 'Status',
            '#ts': 'Timestamp'
        },
        ExpressionAttributeValues={
            ':deleted': 'DELETED',
            ':stopped': 'STOPPED',
            ':start': month_start_ts,
            ':end': month_end_ts
        }
    )
    
    deleted_resources.extend(response.get('Items', []))
    
    # Handle pagination
    while 'LastEvaluatedKey' in response:
        response = table.scan(
            FilterExpression='#status IN (:deleted, :stopped) AND #ts BETWEEN :start AND :end',
            ExpressionAttributeNames={
                '#status': 'Status',
                '#ts': 'Timestamp'
            },
            ExpressionAttributeValues={
                ':deleted': 'DELETED',
                ':stopped': 'STOPPED',
                ':start': month_start_ts,
                ':end': month_end_ts
            },
            ExclusiveStartKey=response['LastEvaluatedKey']
        )
        deleted_resources.extend(response.get('Items', []))
    
    print(f"  Found {len(deleted_resources)} deleted/stopped resources")
    
    # Calculate savings by service
    savings_by_service = {}
    detailed_resources = []
    
    for resource in deleted_resources:
        resource_type = resource.get('ResourceType', 'Unknown')
        resource_id = resource.get('ResourceId', 'Unknown')
        timestamp = int(resource.get('Timestamp', 0))
        
        # Get instance type (or use resource type for services without instance types)
        if resource_type in ['NAT_GATEWAY', 'ALB', 'NLB', 'ELB', 'EIP', 'VPC_ENDPOINT', 'S3_BUCKET', 'VPC', 'SUBNET']:
            instance_type = resource_type  # Use resource type itself
        elif resource_type == 'EBS':
            # EBS volumes - get volume type or size
            instance_type = resource.get('VolumeType', 'gp3')
        else:
            instance_type = resource.get('InstanceType', resource.get('DBInstanceClass', 'Unknown'))
        
        # Calculate savings
        monthly_savings = calculate_monthly_savings(resource_type, instance_type, AWS_REGION)
        
        if monthly_savings > 0:
            # Aggregate by service
            if resource_type not in savings_by_service:
                savings_by_service[resource_type] = {
                    'count': 0,
                    'savings': 0.0
                }
            
            savings_by_service[resource_type]['count'] += 1
            savings_by_service[resource_type]['savings'] += monthly_savings
            
            # Add to detailed list
            detailed_resources.append({
                'date': datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d'),
                'resource_id': resource_id,
                'resource_type': resource_type,
                'instance_type': instance_type,
                'monthly_savings': monthly_savings
            })
    
    # Calculate totals
    total_savings = sum(s['savings'] for s in savings_by_service.values())
    total_resources = len(detailed_resources)
    
    print(f"  💰 Total savings: ${total_savings:.2f}/month")
    print(f"  🗑️  Total resources: {total_resources}")
    
    return {
        'month': month,
        'total_savings': total_savings,
        'total_resources': total_resources,
        'savings_by_service': savings_by_service,
        'detailed_resources': detailed_resources,
        'generated_at': datetime.now().isoformat()
    }

def generate_dashboard_data(savings_data):
    """
    Generate dashboard-ready JSON data
    
    Args:
        savings_data: Calculated savings data
    
    Returns:
        Dashboard data structure
    """
    print("📝 Generating dashboard data...")
    
    # Get historical data (last 6 months)
    historical_data = get_historical_data(6)
    
    # Calculate cumulative savings
    cumulative_savings = calculate_cumulative_savings()
    
    # Format data for dashboard
    dashboard = {
        'current_month': {
            'month': savings_data['month'],
            'total_savings': round(savings_data['total_savings'], 2),
            'total_resources': savings_data['total_resources'],
            'savings_by_service': []
        },
        'breakdown': [],
        'historical': historical_data,
        'cumulative_savings': round(cumulative_savings, 2),
        'resources_detail': savings_data['detailed_resources'],
        'last_updated': datetime.now().isoformat()
    }
    
    # Format savings by service
    for service, data in savings_data['savings_by_service'].items():
        dashboard['current_month']['savings_by_service'].append({
            'service': SERVICE_NAMES.get(service, service),
            'service_code': service,
            'count': data['count'],
            'savings': round(data['savings'], 2)
        })
    
    # Sort by savings (highest first)
    dashboard['current_month']['savings_by_service'].sort(
        key=lambda x: x['savings'],
        reverse=True
    )
    
    # Create breakdown for pie chart
    for item in dashboard['current_month']['savings_by_service']:
        dashboard['breakdown'].append({
            'name': item['service'],
            'value': item['savings']
        })
    
    return dashboard

def get_historical_data(months=6):
    """
    Get historical savings data for last N months
    
    Args:
        months: Number of months to retrieve
    
    Returns:
        List of historical data points
    """
    print(f"📈 Getting historical data (last {months} months)...")
    
    historical = []
    current_date = datetime.now()
    
    for i in range(months, 0, -1):
        # Calculate month
        target_month = current_date.month - i
        target_year = current_date.year
        
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        
        month_str = f"{target_year}-{target_month:02d}"
        
        # Try to load from S3 archive, or calculate if current month
        if month_str == current_date.strftime('%Y-%m'):
            savings_data = calculate_savings(month_str)
            total = savings_data['total_savings']
        else:
            total = get_archived_month_savings(month_str)
        
        historical.append({
            'month': month_str,
            'month_name': datetime(target_year, target_month, 1).strftime('%b %Y'),
            'savings': round(total, 2)
        })
    
    return historical

def get_archived_month_savings(month):
    """
    Get savings from archived month data
    
    Args:
        month: YYYY-MM format
    
    Returns:
        Total savings for that month
    """
    try:
        # Try to read from S3 archive
        key = f"dashboard/reports/{month}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        data = json.loads(response['Body'].read())
        return data.get('total_savings', 0.0)
    except:
        # If not found, return 0
        return 0.0

def calculate_cumulative_savings():
    """
    Calculate total cumulative savings (all time)
    
    Returns:
        Total cumulative savings
    """
    print("💎 Calculating cumulative savings...")
    
    try:
        # Scan all deleted/stopped resources (could cache this)
        total = 0.0
        
        response = table.scan(
            FilterExpression='#status IN (:deleted, :stopped)',
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={':deleted': 'DELETED', ':stopped': 'STOPPED'},
            ProjectionExpression='ResourceType, InstanceType'
        )
        
        for item in response.get('Items', []):
            resource_type = item.get('ResourceType', 'Unknown')
            instance_type = item.get('InstanceType', 'Unknown')
            total += calculate_monthly_savings(resource_type, instance_type, AWS_REGION)
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                FilterExpression='#status IN (:deleted, :stopped)',
                ExpressionAttributeNames={'#status': 'Status'},
                ExpressionAttributeValues={':deleted': 'DELETED', ':stopped': 'STOPPED'},
                ProjectionExpression='ResourceType, InstanceType',
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            
            for item in response.get('Items', []):
                resource_type = item.get('ResourceType', 'Unknown')
                instance_type = item.get('InstanceType', 'Unknown')
                total += calculate_monthly_savings(resource_type, instance_type, AWS_REGION)
        
        return total
    
    except Exception as e:
        print(f"  ⚠️  Error calculating cumulative: {str(e)}")
        return 0.0

def upload_dashboard_data(dashboard_data):
    """
    Upload dashboard data to S3
    
    Args:
        dashboard_data: Dashboard JSON data
    """
    print("📤 Uploading dashboard data to S3...")
    
    try:
        # Upload current month data
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key='dashboard/data.json',
            Body=json.dumps(dashboard_data, indent=2, cls=DecimalEncoder),
            ContentType='application/json',
            CacheControl='no-cache, no-store, must-revalidate'
        )
        
        print(f"  ✅ Uploaded to s3://{S3_BUCKET}/dashboard/data.json")
    
    except Exception as e:
        print(f"  ❌ Upload failed: {str(e)}")
        raise

def is_month_end():
    """Check if today is the last day of the month"""
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    return tomorrow.month != today.month

def archive_monthly_report(savings_data):
    """
    Archive monthly report to S3
    
    Args:
        savings_data: Monthly savings data
    """
    print("📦 Archiving monthly report...")
    
    try:
        month = savings_data['month']
        
        # Save JSON report
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=f'dashboard/reports/{month}.json',
            Body=json.dumps(savings_data, indent=2, cls=DecimalEncoder),
            ContentType='application/json'
        )
        
        # Generate CSV report
        csv_data = generate_csv_report(savings_data)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=f'dashboard/reports/{month}.csv',
            Body=csv_data,
            ContentType='text/csv'
        )
        
        print(f"  ✅ Archived report for {month}")
    
    except Exception as e:
        print(f"  ❌ Archive failed: {str(e)}")

def generate_csv_report(savings_data):
    """
    Generate CSV report from savings data
    
    Args:
        savings_data: Monthly savings data
    
    Returns:
        CSV formatted string
    """
    lines = []
    
    # Header
    lines.append("Date,Resource ID,Resource Type,Instance Type,Monthly Savings")
    
    # Data rows
    for resource in savings_data['detailed_resources']:
        lines.append(
            f"{resource['date']},"
            f"{resource['resource_id']},"
            f"{resource['resource_type']},"
            f"{resource['instance_type']},"
            f"${resource['monthly_savings']:.2f}"
        )
    
    # Summary
    lines.append("")
    lines.append(f"Total Savings,,,${savings_data['total_savings']:.2f}")
    lines.append(f"Total Resources,,,{savings_data['total_resources']}")
    
    return '\n'.join(lines)