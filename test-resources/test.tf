# test.tf - Create 2 of each resource type for CostGuardian testing

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region for test resources"
  type        = string
  default     = "us-west-1"
}

variable "test_prefix" {
  description = "Prefix for test resource names"
  type        = string
  default     = "costguardian-test-2x"
}

variable "resource_count" {
  description = "Number of each resource type to create"
  type        = number
  default     = 2
}

# =============================================================================
# Shared Networking (VPC + 2 subnets + IGW)
# =============================================================================

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "test_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.test_prefix}-vpc"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "CostGuardian-Testing"
  }
}

resource "aws_subnet" "test_subnet" {
  count             = 2
  vpc_id            = aws_vpc.test_vpc.id
  cidr_block        = "10.0.${count.index}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name        = "${var.test_prefix}-subnet-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_internet_gateway" "test_igw" {
  vpc_id = aws_vpc.test_vpc.id

  tags = {
    Name        = "${var.test_prefix}-igw"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

# =============================================================================
# 1) Elastic IPs (2) - unattached
# =============================================================================

resource "aws_eip" "test_eip" {
  count  = var.resource_count
  domain = "vpc"

  tags = {
    Name        = "${var.test_prefix}-eip-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

# =============================================================================
# 2) NAT Gateways (2) + their EIPs
# =============================================================================

resource "aws_eip" "nat_eip" {
  count  = var.resource_count
  domain = "vpc"

  tags = {
    Name        = "${var.test_prefix}-nat-eip-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_nat_gateway" "test_nat" {
  count         = var.resource_count
  allocation_id = aws_eip.nat_eip[count.index].id
  subnet_id     = aws_subnet.test_subnet[0].id

  tags = {
    Name        = "${var.test_prefix}-nat-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }

  depends_on = [aws_internet_gateway.test_igw]
}

# =============================================================================
# 3) EC2 Instances (2)
# =============================================================================

data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "test_ec2" {
  count         = var.resource_count
  ami           = data.aws_ami.amazon_linux_2.id
  instance_type = "t3.micro"
  subnet_id     = aws_subnet.test_subnet[0].id

  tags = {
    Name        = "${var.test_prefix}-ec2-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }

  lifecycle {
    ignore_changes = [ami]
  }
}

# =============================================================================
# 4) EBS Volumes (2) - unattached
# =============================================================================

resource "aws_ebs_volume" "test_volume" {
  count             = var.resource_count
  availability_zone = data.aws_availability_zones.available.names[0]
  size              = 8

  tags = {
    Name        = "${var.test_prefix}-ebs-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

# =============================================================================
# 5) RDS Instances (2)
# =============================================================================

resource "aws_db_subnet_group" "test_db_subnet" {
  name       = "${var.test_prefix}-db-subnet"
  subnet_ids = aws_subnet.test_subnet[*].id

  tags = {
    Name        = "${var.test_prefix}-db-subnet-group"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_security_group" "test_rds_sg" {
  name        = "${var.test_prefix}-rds-sg"
  description = "Security group for test RDS instances"
  vpc_id      = aws_vpc.test_vpc.id

  ingress {
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.test_prefix}-rds-sg"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_db_instance" "test_rds" {
  count                  = var.resource_count
  identifier             = "${var.test_prefix}-rds-${count.index + 1}"
  engine                 = "mysql"
  engine_version         = "8.0"
  instance_class         = "db.t3.micro"
  allocated_storage      = 20
  storage_type           = "gp2"
  username               = "admin"
  password               = "TestPassword123!" # change for real use
  skip_final_snapshot    = true
  db_subnet_group_name   = aws_db_subnet_group.test_db_subnet.name
  vpc_security_group_ids = [aws_security_group.test_rds_sg.id]
  publicly_accessible    = false

  tags = {
    Name        = "${var.test_prefix}-rds-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

# =============================================================================
# 6) Load Balancers (2) + Target Groups (2) + Listeners (2)
# =============================================================================

resource "aws_security_group" "test_alb_sg" {
  name        = "${var.test_prefix}-alb-sg"
  description = "Security group for test ALB"
  vpc_id      = aws_vpc.test_vpc.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.test_prefix}-alb-sg"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_lb" "test_alb" {
  count              = var.resource_count
  name               = "${var.test_prefix}-alb-${count.index + 1}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.test_alb_sg.id]
  subnets            = aws_subnet.test_subnet[*].id

  tags = {
    Name        = "${var.test_prefix}-alb-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

resource "aws_lb_target_group" "test_tg" {
  count    = var.resource_count
  name     = "${var.test_prefix}-tg-${count.index + 1}"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.test_vpc.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200"
    path                = "/"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 2
  }

  tags = {
    Name        = "${var.test_prefix}-tg-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_lb_listener" "test_listener" {
  count             = var.resource_count
  load_balancer_arn = aws_lb.test_alb[count.index].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.test_tg[count.index].arn
  }
}

# =============================================================================
# 7) EBS Snapshots (2)
# =============================================================================

resource "aws_ebs_snapshot" "test_snapshot" {
  count       = var.resource_count
  volume_id   = aws_ebs_volume.test_volume[count.index].id
  description = "Test snapshot for CostGuardian - ${count.index + 1}"

  tags = {
    Name        = "${var.test_prefix}-snapshot-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

# =============================================================================
# 8) S3 Buckets (2)
# =============================================================================

resource "random_id" "bucket_suffix" {
  count       = var.resource_count
  byte_length = 4
}

resource "aws_s3_bucket" "test_bucket" {
  count  = var.resource_count
  bucket = "${var.test_prefix}-bucket-${count.index + 1}-${random_id.bucket_suffix[count.index].hex}"

  tags = {
    Name        = "${var.test_prefix}-bucket-${count.index + 1}"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

# =============================================================================
# Outputs
# =============================================================================

output "created_resources" {
  value = {
    vpc_id           = aws_vpc.test_vpc.id
    subnets          = aws_subnet.test_subnet[*].id

    elastic_ip_ids   = aws_eip.test_eip[*].id
    nat_gateway_ids  = aws_nat_gateway.test_nat[*].id
    ec2_instance_ids = aws_instance.test_ec2[*].id
    ebs_volume_ids   = aws_ebs_volume.test_volume[*].id
    rds_instance_ids = aws_db_instance.test_rds[*].id

    alb_arns         = aws_lb.test_alb[*].arn
    target_group_arns = aws_lb_target_group.test_tg[*].arn
    listener_arns    = aws_lb_listener.test_listener[*].arn

    snapshot_ids     = aws_ebs_snapshot.test_snapshot[*].id
    s3_bucket_names  = aws_s3_bucket.test_bucket[*].bucket
  }
}