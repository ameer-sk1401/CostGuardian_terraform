
# Resource Identifiers

output "s3_bucket_name" {
  description = "Name of the S3 bucket for backups"
  value       = aws_s3_bucket.backups.id
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket"
  value       = aws_s3_bucket.backups.arn
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table for resource logging"
  value       = aws_dynamodb_table.resource_log.name
}

output "dynamodb_table_arn" {
  description = "ARN of the DynamoDB table"
  value       = aws_dynamodb_table.resource_log.arn
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for alerts"
  value       = aws_sns_topic.alerts.arn
}

output "sns_topic_name" {
  description = "Name of the SNS topic"
  value       = aws_sns_topic.alerts.name
}

output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.costguardian.function_name
}

output "lambda_function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.costguardian.arn
}

output "lambda_role_arn" {
  description = "ARN of the Lambda IAM role"
  value       = aws_iam_role.lambda.arn
}

output "lambda_role_name" {
  description = "Name of the Lambda IAM role"
  value       = aws_iam_role.lambda.name
}


# EventBridge Configuration

output "eventbridge_rule_name" {
  description = "Name of the EventBridge rule for scheduled scans"
  value       = aws_cloudwatch_event_rule.daily_scan.name
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule"
  value       = aws_cloudwatch_event_rule.daily_scan.arn
}

output "scan_schedule" {
  description = "Cron schedule for resource scans"
  value       = var.scan_schedule
}


# CloudWatch Configuration

output "lambda_log_group_name" {
  description = "Name of the CloudWatch Log Group for Lambda"
  value       = aws_cloudwatch_log_group.lambda.name
}

output "lambda_cloudwatch_logs_url" {
  description = "Direct URL to Lambda CloudWatch Logs"
  value       = "https://${data.aws_region.current.name}.console.aws.amazon.com/cloudwatch/home?region=${data.aws_region.current.name}#logsV2:log-groups/log-group/${replace(aws_cloudwatch_log_group.lambda.name, "/", "$252F")}"
}

output "github_actions_role_arn" {
  description = "IAM Role ARN for GitHub Actions OIDC authentication"
  value       = aws_iam_role.github_actions.arn
}

# Configuration Summary

output "environment_variables" {
  description = "Lambda environment variables (for CI/CD reference)"
  value = {
    S3_BUCKET_NAME      = aws_s3_bucket.backups.id
    DYNAMODB_TABLE      = aws_dynamodb_table.resource_log.name
    SNS_TOPIC_ARN       = aws_sns_topic.alerts.arn
    IDLE_THRESHOLD_DAYS = var.idle_threshold_days
    ENVIRONMENT         = var.environment
    LOG_LEVEL           = var.lambda_log_level
  }
  sensitive = false
}

output "deployment_region" {
  description = "AWS region where resources were deployed"
  value       = data.aws_region.current.name
}

output "account_id" {
  description = "AWS account ID where resources were deployed"
  value       = data.aws_caller_identity.current.account_id
}


# CI/CD Integration Outputs

output "lambda_update_command" {
  description = "AWS CLI command to update Lambda function code (for CI/CD)"
  value       = "aws lambda update-function-code --function-name ${aws_lambda_function.costguardian.function_name} --zip-file fileb://lambda_function.zip"
}

output "ci_cd_environment_variables" {
  description = "Environment variables to set in CI/CD pipeline"
  value = {
    AWS_REGION           = data.aws_region.current.name
    LAMBDA_FUNCTION_NAME = aws_lambda_function.costguardian.function_name
    S3_BUCKET_NAME       = aws_s3_bucket.backups.id
  }
}

output "cost_savings_lambda_name" {
  description = "Name of the cost savings calculator Lambda function"
  value       = aws_lambda_function.cost_savings.function_name
}

output "dashboard_url" {
  description = "URL to access the CostGuardian savings dashboard"
  value       = "http://${aws_s3_bucket.backups.bucket}.s3-website-${data.aws_region.current.name}.amazonaws.com/dashboard/"
}


# Post-Deployment Instructions

output "next_steps" {
  description = "Post-deployment instructions"
  value       = <<-EOT
    
    ═══════════════════════════════════════════════════════════════════════
    🎉 CostGuardian Infrastructure Deployed Successfully!
    ═══════════════════════════════════════════════════════════════════════
    
    📋 NEXT STEPS:
    
    1️⃣  CONFIRM EMAIL SUBSCRIPTIONS
       → Check your inbox for SNS confirmation emails
       → Click the confirmation link in each email
       → Emails: ${join(", ", var.alert_emails)}
    
    2️⃣  UPLOAD LAMBDA CODE
       → Package your Lambda code: cd lambda && zip -r lambda_function.zip .
       → Update function: ${format("aws lambda update-function-code --function-name %s --zip-file fileb://lambda_function.zip", aws_lambda_function.costguardian.function_name)}
       → Or use your CI/CD pipeline (see README)
    
    3️⃣  TEST THE SYSTEM
       → Trigger manually: ${format("aws lambda invoke --function-name %s --payload '{}' response.json", aws_lambda_function.costguardian.function_name)}
       → Check logs: ${format("aws logs tail /aws/lambda/%s --follow", aws_lambda_function.costguardian.function_name)}
    
    4️⃣  VERIFY RESOURCES
       → S3 Bucket: ${aws_s3_bucket.backups.id}
       → DynamoDB Table: ${aws_dynamodb_table.resource_log.name}
       → Lambda Function: ${aws_lambda_function.costguardian.function_name}
       → EventBridge Rule: ${aws_cloudwatch_event_rule.daily_scan.name}
    
    📊 MONITORING
       → CloudWatch Logs: ${format("https://%s.console.aws.amazon.com/cloudwatch/home?region=%s#logsV2:log-groups/log-group/%s", data.aws_region.current.name, data.aws_region.current.name, replace(aws_cloudwatch_log_group.lambda.name, "/", "$252F"))}
       → DynamoDB Console: ${format("https://%s.console.aws.amazon.com/dynamodbv2/home?region=%s#table?name=%s", data.aws_region.current.name, data.aws_region.current.name, aws_dynamodb_table.resource_log.name)}
       → S3 Console: ${format("https://s3.console.aws.amazon.com/s3/buckets/%s", aws_s3_bucket.backups.id)}
    
    💡 TIPS
       → The Lambda runs on schedule: ${var.scan_schedule}
       → Idle threshold is set to: ${var.idle_threshold_days} days
       → Check README.md for detailed usage instructions
    
    ═══════════════════════════════════════════════════════════════════════
  EOT
}
