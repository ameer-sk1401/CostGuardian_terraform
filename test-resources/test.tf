# test.tf - Minimal single-resource version of CostGuardian test stack

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
  default     = "costguardian-test-single"
}


# VPC and Networking (shared)


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
    Purpose     = "CostGuardian-Single-Testing"
  }
}

# ALB requires at least two subnets in different AZs
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


# 1. Elastic IP (unattached)


resource "aws_eip" "test_eip" {
  domain = "vpc"

  tags = {
    Name        = "${var.test_prefix}-eip-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

# NAT EIP (for the NAT gateway)
resource "aws_eip" "nat_eip" {
  domain = "vpc"

  tags = {
    Name        = "${var.test_prefix}-nat-eip-1"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}


# 2. NAT Gateway


resource "aws_nat_gateway" "test_nat" {
  allocation_id = aws_eip.nat_eip.id
  subnet_id     = aws_subnet.test_subnet[0].id

  tags = {
    Name        = "${var.test_prefix}-nat-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }

  depends_on = [aws_internet_gateway.test_igw]
}


# 3. EC2 instance


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
  ami           = data.aws_ami.amazon_linux_2.id
  instance_type = "t3.micro"
  subnet_id     = aws_subnet.test_subnet[0].id

  tags = {
    Name        = "${var.test_prefix}-ec2-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }

  lifecycle {
    ignore_changes = [ami]
  }
}


# 4. EBS Volume (unattached)


resource "aws_ebs_volume" "test_volume" {
  availability_zone = data.aws_availability_zones.available.names[0]
  size              = 8

  tags = {
    Name        = "${var.test_prefix}-ebs-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}


# 5. RDS Instance


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
  description = "Security group for test RDS instance"
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
  identifier             = "${var.test_prefix}-rds-1"
  engine                 = "mysql"
  engine_version         = "8.0"
  instance_class         = "db.t3.micro"
  allocated_storage      = 20
  storage_type           = "gp2"
  username               = "admin"
  password               = "TestPassword123!"
  skip_final_snapshot    = true
  db_subnet_group_name   = aws_db_subnet_group.test_db_subnet.name
  vpc_security_group_ids = [aws_security_group.test_rds_sg.id]
  publicly_accessible    = false

  tags = {
    Name        = "${var.test_prefix}-rds-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}


# 6. Load Balancer (ALB + TG + Listener)


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
  name               = "${var.test_prefix}-alb-1"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.test_alb_sg.id]
  subnets            = aws_subnet.test_subnet[*].id

  tags = {
    Name        = "${var.test_prefix}-alb-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}

resource "aws_lb_target_group" "test_tg" {
  name     = "${var.test_prefix}-tg-1"
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
    Name        = "${var.test_prefix}-tg-1"
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_lb_listener" "test_listener" {
  load_balancer_arn = aws_lb.test_alb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.test_tg.arn
  }
}


# 7. EBS Snapshot


resource "aws_ebs_snapshot" "test_snapshot" {
  volume_id   = aws_ebs_volume.test_volume.id
  description = "Test snapshot for CostGuardian - 1"

  tags = {
    Name        = "${var.test_prefix}-snapshot-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}


# 8. S3 Bucket (empty)


resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "test_bucket" {
  bucket = "${var.test_prefix}-bucket-1-${random_id.bucket_suffix.hex}"

  tags = {
    Name        = "${var.test_prefix}-bucket-1"
    Environment = "test"
    ManagedBy   = "terraform"
    Purpose     = "idle-test"
    CreatedAt   = timestamp()
  }
}


# Simple outputs


output "created_resources" {
  value = {
    vpc_id           = aws_vpc.test_vpc.id
    subnets          = aws_subnet.test_subnet[*].id
    elastic_ip_id    = aws_eip.test_eip.id
    nat_gateway_id   = aws_nat_gateway.test_nat.id
    ec2_instance_id  = aws_instance.test_ec2.id
    ebs_volume_id    = aws_ebs_volume.test_volume.id
    rds_instance_id  = aws_db_instance.test_rds.id
    alb_arn          = aws_lb.test_alb.arn
    target_group_arn = aws_lb_target_group.test_tg.arn
    alb_listener_arn = aws_lb_listener.test_listener.arn
    ebs_snapshot_id  = aws_ebs_snapshot.test_snapshot.id
    s3_bucket_name   = aws_s3_bucket.test_bucket.bucket
  }
}