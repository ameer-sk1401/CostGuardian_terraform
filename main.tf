terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

 
# Provider Configuration
 
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "CostGuardian"
      ManagedBy   = "Terraform"
      Environment = var.environment
    }
  }
}

 
# Data Sources
 
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

 
# Local Variables
 
locals {
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.name
  name_prefix = "costguardian-${var.environment}"
}

 
# S3 Bucket for Backups
 
resource "aws_s3_bucket" "backups" {
  bucket = "${local.name_prefix}-backups-${local.account_id}"

  tags = {
    Name        = "${local.name_prefix}-backups"
    Project     = "CostGuardian"
    Environment = var.environment
    Purpose     = "Store resource configuration backups"
    CostGuardianBucket = "Protected"
  }
}

# Enable versioning
resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Enable encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block public access
resource "aws_s3_bucket_public_access_block" "backups" {
  bucket = aws_s3_bucket.backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle policy
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "transition-old-backups"
    status = "Enabled"

    # Required: filter (even if empty)
    filter {}

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 180
      storage_class = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

 
# DynamoDB Table for Resource Tracking
 
resource "aws_dynamodb_table" "resource_log" {
  name         = "${local.name_prefix}-resource-log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ResourceId"
  range_key    = "Timestamp"

  attribute {
    name = "ResourceId"
    type = "S"
  }

  attribute {
    name = "Timestamp"
    type = "N"
  }

  attribute {
    name = "ResourceType"
    type = "S"
  }

  attribute {
    name = "Status"
    type = "S"
  }

  # Global Secondary Index for querying by type and status
  global_secondary_index {
    name            = "ResourceTypeStatusIndex"
    hash_key        = "ResourceType"
    range_key       = "Status"
    projection_type = "ALL"
  }

  # Point-in-time recovery
  point_in_time_recovery {
    enabled = var.enable_pitr
  }

  # Server-side encryption
  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-resource-log"
    Project     = "CostGuardian"
    Environment = var.environment
    Purpose     = "Track resource lifecycle"
  }
}

 
# SNS Topic for Alerts
 
resource "aws_sns_topic" "alerts" {
  name         = "${local.name_prefix}-alerts"
  display_name = "CostGuardian"

  tags = {
    Name        = "${local.name_prefix}-alerts"
    Project     = "CostGuardian"
    Environment = var.environment
    Purpose     = "Email alerts for idle/deleted resources"
  }
}

# SNS Topic Policy
resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowLambdaPublish"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.alerts.arn
      }
    ]
  })
}

# Email subscriptions
resource "aws_sns_topic_subscription" "email_alerts" {
  count     = length(var.alert_emails)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_emails[count.index]
}

 
# IAM Role for Lambda
 
resource "aws_iam_role" "lambda" {
  name = "${local.name_prefix}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "${local.name_prefix}-lambda-role"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

# Lambda IAM Policy
resource "aws_iam_role_policy" "lambda_permissions" {
  name = "${local.name_prefix}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudWatch Logs
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-*"
      },
      # EC2 Operations (your Lambda code uses these)
      {
        Sid    = "EC2Operations"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "ec2:DescribeVolumes",
          "ec2:DescribeSnapshots",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeVpcs",
          "ec2:DescribeSubnets",
          "ec2:DescribeRouteTables",
          "ec2:DescribeInternetGateways",
          "ec2:DescribeNatGateways",
          "ec2:DescribeVpcEndpoints",
          "ec2:DescribeAddresses",
          "ec2:StopInstances",
          "ec2:TerminateInstances",
          "ec2:DeleteVolume",
          "ec2:DeleteSnapshot",
          "ec2:DeleteSecurityGroup",
          "ec2:DeleteNetworkInterface",
          "ec2:DeleteVpc",
          "ec2:DeleteSubnet",
          "ec2:DeleteRouteTable",
          "ec2:DeleteInternetGateway",
          "ec2:DeleteNatGateway",
          "ec2:DeleteVpcEndpoints",
          "ec2:ReleaseAddress",
          "ec2:DetachInternetGateway",
          "ec2:DisassociateRouteTable",
          "ec2:CreateTags",
          "ec2:CreateSnapshot",
          "ec2:DescribeInstanceAttribute"
        ]
        Resource = "*"
      },
      # RDS Operations
      {
        Sid    = "RDSOperations"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:DescribeDBClusters",
          "rds:StopDBInstance",
          "rds:DeleteDBInstance",
          "rds:CreateDBSnapshot",
          "rds:ListTagsForResource"
        ]
        Resource = "*"
      },
      # ELB Operations (Classic and Application/Network Load Balancers)
      {
        Sid    = "ELBOperations"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
          "elasticloadbalancing:DescribeListeners",
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:DescribeTags"
        ]
        Resource = "*"
      },
      # S3 Operations (for backups and monitoring all buckets)
      {
        Sid    = "S3Operations"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectAcl",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:ListAllMyBuckets",
          "s3:GetBucketTagging",
          "s3:GetLifecycleConfiguration",
          "s3:PutLifecycleConfiguration",
          "s3:DeleteBucket"
        ]
        Resource = [
          aws_s3_bucket.backups.arn,
          "${aws_s3_bucket.backups.arn}/*",
          "arn:aws:s3:::*"
        ]
      },
      # DynamoDB Operations
      {
        Sid    = "DynamoDBOperations"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:DescribeTable"
        ]
        Resource = [
          aws_dynamodb_table.resource_log.arn,
          "${aws_dynamodb_table.resource_log.arn}/index/*"
        ]
      },
      # SNS Publish
      {
        Sid    = "SNSPublish"
        Effect = "Allow"
        Action = [
          "sns:Publish",
          "sns:GetTopicAttributes"
        ]
        Resource = aws_sns_topic.alerts.arn
      },
      # CloudWatch Metrics (your Lambda sends custom metrics)
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "cloudwatch:GetMetricStatistics"
        ]
        Resource = "*"
      }
    ]
  })
}

# Attach AWS managed policy for Lambda basic execution
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

 
# Lambda Function
 
resource "aws_lambda_function" "costguardian" {
  function_name = "${local.name_prefix}-main"
  role          = aws_iam_role.lambda.arn
  handler       = "lambda_handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 512

  # This will be replaced by your CI/CD pipeline
  filename         = "lambda/lambda_function.zip"
  source_code_hash = fileexists("lambda/lambda_function.zip") ? filebase64sha256("lambda/lambda_function.zip") : null

  # Environment variables matching your Lambda code
  environment {
    variables = {
      S3_BUCKET_NAME = aws_s3_bucket.backups.id
      DYNAMODB_TABLE = aws_dynamodb_table.resource_log.name
      SNS_TOPIC_ARN  = aws_sns_topic.alerts.arn
    }
  }

  tags = {
    Name        = "${local.name_prefix}-main"
    Project     = "CostGuardian"
    Environment = var.environment
    Purpose     = "Cost Optimization"
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.costguardian.function_name}"
  retention_in_days = var.lambda_log_retention_days

  tags = {
    Name        = "${local.name_prefix}-lambda-logs"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

 
# EventBridge Rule for Scheduled Trigger
 
resource "aws_cloudwatch_event_rule" "daily_scan" {
  name                = "${local.name_prefix}-daily-scan"
  description         = "Trigger CostGuardian Lambda on schedule"
  schedule_expression = var.scan_schedule

  tags = {
    Name        = "${local.name_prefix}-daily-scan"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.daily_scan.name
  target_id = "CostGuardianLambda"
  arn       = aws_lambda_function.costguardian.arn
}

# Allow EventBridge to invoke Lambda
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.costguardian.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_scan.arn
}
