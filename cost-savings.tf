resource "aws_lambda_function" "cost_savings" {
  function_name = "${local.name_prefix}-cost-savings"
  role          = aws_iam_role.cost_savings_lambda.arn
  handler       = "cost_savings_calculator.lambda_handler"
  runtime       = "python3.11"
  timeout       = 120
  memory_size   = 256

  # Deployment package (will be created by CI/CD)
  filename         = "lambda/cost_savings_function.zip"
  source_code_hash = fileexists("lambda/cost_savings_function.zip") ? filebase64sha256("lambda/cost_savings_function.zip") : null

  # Environment variables
  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.resource_log.name
      S3_BUCKET_NAME = aws_s3_bucket.backups.id
      REGION         = var.aws_region
    }
  }

  tags = {
    Name        = "${local.name_prefix}-cost-savings"
    Project     = "CostGuardian"
    Environment = var.environment
    Purpose     = "Calculate cost savings"
  }
}

# CloudWatch Log Group for Cost Savings Lambda
resource "aws_cloudwatch_log_group" "cost_savings_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.cost_savings.function_name}"
  retention_in_days = var.lambda_log_retention_days

  tags = {
    Name        = "${local.name_prefix}-cost-savings-logs"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

 
# IAM Role for Cost Savings Lambda
 
resource "aws_iam_role" "cost_savings_lambda" {
  name = "${local.name_prefix}-cost-savings-role"

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
    Name        = "${local.name_prefix}-cost-savings-role"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

# IAM Policy for Cost Savings Lambda
resource "aws_iam_role_policy" "cost_savings_lambda_permissions" {
  name = "${local.name_prefix}-cost-savings-policy"
  role = aws_iam_role.cost_savings_lambda.id

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
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-cost-savings*"
      },
      # DynamoDB Read
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = [
          "dynamodb:Scan",
          "dynamodb:Query",
          "dynamodb:GetItem"
        ]
        Resource = [
          aws_dynamodb_table.resource_log.arn,
          "${aws_dynamodb_table.resource_log.arn}/index/*"
        ]
      },
      # S3 Write to Dashboard folder
      {
        Sid    = "S3DashboardWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.backups.arn}/dashboard/*",
          aws_s3_bucket.backups.arn
        ]
      }
    ]
  })
}

# Attach AWS managed policy for Lambda basic execution
resource "aws_iam_role_policy_attachment" "cost_savings_lambda_basic" {
  role       = aws_iam_role.cost_savings_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

 
# EventBridge Rule for Hourly Trigger
 
resource "aws_cloudwatch_event_rule" "hourly_savings_update" {
  name                = "${local.name_prefix}-hourly-savings"
  description         = "Trigger cost savings calculator hourly"
  schedule_expression = "rate(1 hour)"

  tags = {
    Name        = "${local.name_prefix}-hourly-savings"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "cost_savings_lambda" {
  rule      = aws_cloudwatch_event_rule.hourly_savings_update.name
  target_id = "CostSavingsLambda"
  arn       = aws_lambda_function.cost_savings.arn
}

# Allow EventBridge to invoke Cost Savings Lambda
resource "aws_lambda_permission" "allow_eventbridge_cost_savings" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_savings.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.hourly_savings_update.arn
}

 
# S3 Bucket Policy for Dashboard Website
 
resource "aws_s3_bucket_policy" "dashboard_access" {
  bucket = aws_s3_bucket.backups.id

  # Depends on public access block being updated first
  depends_on = [aws_s3_bucket_public_access_block.backups]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadDashboard"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.backups.arn}/dashboard/*"
      }
    ]
  })
}

 
# S3 Bucket Website Configuration
 
resource "aws_s3_bucket_website_configuration" "dashboard" {
  bucket = aws_s3_bucket.backups.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "error.html"
  }
}

 
# Upload Dashboard Files to S3
 

# Upload dashboard HTML
resource "aws_s3_object" "dashboard_html" {
  bucket       = aws_s3_bucket.backups.id
  key          = "dashboard/index.html"
  source       = "dashboard/index.html"
  content_type = "text/html"
  etag         = filemd5("dashboard/index.html")

  tags = {
    Name        = "dashboard-html"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

# Upload dashboard JavaScript
resource "aws_s3_object" "dashboard_js" {
  bucket       = aws_s3_bucket.backups.id
  key          = "dashboard/dashboard.js"
  source       = "dashboard/dashboard.js"
  content_type = "application/javascript"
  etag         = filemd5("dashboard/dashboard.js")

  tags = {
    Name        = "dashboard-js"
    Project     = "CostGuardian"
    Environment = var.environment
  }
}

