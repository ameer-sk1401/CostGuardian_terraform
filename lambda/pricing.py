
# Hourly rates in USD
AWS_PRICING = {
    'us-east-1': {
        # EC2 Instance Pricing (On-Demand, Linux)
        'EC2': {
            # T2 Family
            't2.nano': 0.0058,
            't2.micro': 0.0116,
            't2.small': 0.023,
            't2.medium': 0.0464,
            't2.large': 0.0928,
            't2.xlarge': 0.1856,
            't2.2xlarge': 0.3712,
            
            # T3 Family
            't3.nano': 0.0052,
            't3.micro': 0.0104,
            't3.small': 0.0208,
            't3.medium': 0.0416,
            't3.large': 0.0832,
            't3.xlarge': 0.1664,
            't3.2xlarge': 0.3328,
            
            # T4g Family (ARM)
            't4g.nano': 0.0042,
            't4g.micro': 0.0084,
            't4g.small': 0.0168,
            't4g.medium': 0.0336,
            't4g.large': 0.0672,
            
            # M5 Family
            'm5.large': 0.096,
            'm5.xlarge': 0.192,
            'm5.2xlarge': 0.384,
            'm5.4xlarge': 0.768,
            
            # C5 Family (Compute Optimized)
            'c5.large': 0.085,
            'c5.xlarge': 0.17,
            'c5.2xlarge': 0.34,
            
            # R5 Family (Memory Optimized)
            'r5.large': 0.126,
            'r5.xlarge': 0.252,
            'r5.2xlarge': 0.504,
        },
        
        # RDS Instance Pricing (On-Demand)
        'RDS': {
            # MySQL/PostgreSQL/MariaDB
            'db.t3.micro': 0.017,
            'db.t3.small': 0.034,
            'db.t3.medium': 0.068,
            'db.t3.large': 0.136,
            'db.t3.xlarge': 0.272,
            'db.t3.2xlarge': 0.544,
            
            'db.t4g.micro': 0.016,
            'db.t4g.small': 0.032,
            'db.t4g.medium': 0.064,
            'db.t4g.large': 0.128,
            
            'db.m5.large': 0.192,
            'db.m5.xlarge': 0.384,
            'db.m5.2xlarge': 0.768,
            
            'db.r5.large': 0.24,
            'db.r5.xlarge': 0.48,
            'db.r5.2xlarge': 0.96,
        },
        
        # NAT Gateway
        'NAT_GATEWAY': 0.045,  # per hour
        
        # Application Load Balancer
        'ALB': 0.0225,  # per hour
        
        # Network Load Balancer
        'NLB': 0.0225,  # per hour
        
        # Classic Load Balancer
        'ELB': 0.025,  # per hour
        
        # EBS Volumes (per GB per month)
        'EBS': {
            'gp3': 0.08,   # General Purpose SSD
            'gp2': 0.10,   # General Purpose SSD
            'io1': 0.125,  # Provisioned IOPS SSD
            'io2': 0.125,  # Provisioned IOPS SSD
            'st1': 0.045,  # Throughput Optimized HDD
            'sc1': 0.015,  # Cold HDD
        },
        
        # Elastic IP (when not attached)
        'EIP': 0.005,  # per hour when not associated
        
        # VPC Endpoints (Interface)
        'VPC_ENDPOINT': 0.01,  # per hour per AZ
        
        # S3 Buckets (storage cost - minimal, mainly saves management overhead)
        'S3_BUCKET': 0.0,  # $0 (storage is per GB, but bucket deletion saves complexity)
        
        # VPC and Subnet (no direct cost, but organizational savings)
        'VPC': 0.0,
        'SUBNET': 0.0,
    }
}

# Monthly multiplier (730 hours average per month)
HOURS_PER_MONTH = 730

def get_hourly_rate(resource_type, instance_type, region='us-west-1'):
    """
    Get hourly rate for a resource
    
    Args:
        resource_type: 'EC2', 'RDS', 'NAT_GATEWAY', etc.
        instance_type: 't2.micro', 'db.t3.small', etc.
        region: AWS region (default us-east-1)
    
    Returns:
        Hourly rate in USD, or None if not found
    """
    try:
        region_pricing = AWS_PRICING.get(region, {})
        
        # Resources with fixed hourly rates (no instance type needed)
        if resource_type in ['NAT_GATEWAY', 'ALB', 'NLB', 'ELB', 'EIP', 'VPC_ENDPOINT', 'S3_BUCKET', 'VPC', 'SUBNET']:
            return region_pricing.get(resource_type, 0.0)
        
        # Resources with instance-specific pricing
        service_pricing = region_pricing.get(resource_type, {})
        return service_pricing.get(instance_type, 0.0)
    
    except Exception as e:
        print(f"Error getting pricing: {e}")
        return 0.0

def calculate_monthly_savings(resource_type, instance_type, region='us-west-1'):
    """
    Calculate monthly savings for a resource
    
    Args:
        resource_type: 'EC2', 'RDS', etc.
        instance_type: 't2.micro', etc.
        region: AWS region
    
    Returns:
        Monthly savings in USD
    """
    hourly_rate = get_hourly_rate(resource_type, instance_type, region)
    return hourly_rate * HOURS_PER_MONTH

def format_currency(amount):
    """Format amount as USD currency"""
    return f"${amount:,.2f}"

# Service display names
SERVICE_NAMES = {
    'EC2': 'EC2 Instances',
    'RDS': 'RDS Databases',
    'NAT_GATEWAY': 'NAT Gateways',
    'ALB': 'Application Load Balancers',
    'NLB': 'Network Load Balancers',
    'ELB': 'Classic Load Balancers',
    'EBS': 'EBS Volumes',
    'EIP': 'Elastic IPs',
    'VPC': 'VPCs',
    'VPC_ENDPOINT': 'VPC Endpoints',
    'S3': 'S3 Buckets',
    'S3_BUCKET': 'S3 Buckets',  # Handle both S3 and S3_BUCKET
    'SUBNET': 'Subnets'
}