
# General Configuration

variable "aws_region" {
  description = "AWS region where resources will be created"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "repository_url" {
  description = "GitHub repository URL for this project"
  type        = string
  default     = "https://github.com/your-username/costguardian"
}


# SNS Alert Configuration

variable "alert_emails" {
  description = "List of email addresses to receive CostGuardian alerts"
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.alert_emails) > 0
    error_message = "At least one alert email must be provided."
  }
}

variable "enable_sns_encryption" {
  description = "Enable KMS encryption for SNS topic"
  type        = bool
  default     = false
}


# Lambda Configuration

variable "lambda_runtime" {
  description = "Lambda runtime (e.g., python3.11, python3.12)"
  type        = string
  default     = "python3.14"
}

variable "lambda_handler" {
  description = "Lambda function handler (format: filename.function_name)"
  type        = string
  default     = "lambda_function.lambda_handler"
}

variable "lambda_timeout" {
  description = "Lambda function timeout in seconds (max 900)"
  type        = number
  default     = 300

  validation {
    condition     = var.lambda_timeout >= 3 && var.lambda_timeout <= 900
    error_message = "Lambda timeout must be between 3 and 900 seconds."
  }
}

variable "lambda_memory_size" {
  description = "Lambda function memory size in MB (128-10240)"
  type        = number
  default     = 512

  validation {
    condition     = var.lambda_memory_size >= 128 && var.lambda_memory_size <= 10240
    error_message = "Lambda memory must be between 128 and 10240 MB."
  }
}

variable "lambda_zip_path" {
  description = "Path to Lambda deployment package (zip file)"
  type        = string
  default     = "lambda/lambda_function.zip"
}

variable "lambda_log_retention_days" {
  description = "CloudWatch Logs retention period in days"
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.lambda_log_retention_days)
    error_message = "Log retention must be a valid CloudWatch Logs retention period."
  }
}

variable "lambda_log_level" {
  description = "Lambda function log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], var.lambda_log_level)
    error_message = "Log level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL."
  }
}


# EventBridge Schedule Configuration

variable "scan_schedule" {
  description = "Cron expression for when to run resource scans (UTC timezone)"
  type        = string
  default     = "cron(0 2 * * ? *)" # Daily at 2 AM UTC

  validation {
    condition     = can(regex("^(rate|cron)\\(.+\\)$", var.scan_schedule))
    error_message = "Schedule must be a valid EventBridge rate or cron expression."
  }
}


# DynamoDB Configuration

variable "enable_pitr" {
  description = "Enable Point-in-Time Recovery for DynamoDB table"
  type        = bool
  default     = true
}

variable "enable_ttl" {
  description = "Enable TTL (Time To Live) for automatic deletion of old DynamoDB records"
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "GitHub repository in format 'owner/repo' for OIDC authentication"
  type        = string
  default     = ""
}

# Resource Detection & Cleanup Configuration

variable "idle_threshold_days" {
  description = "Number of days a resource must be idle before cleanup action"
  type        = number
  default     = 1

  validation {
    condition     = var.idle_threshold_days >= 1 && var.idle_threshold_days <= 90
    error_message = "Idle threshold must be between 1 and 90 days."
  }
}


# Optional Features

variable "enable_xray_tracing" {
  description = "Enable AWS X-Ray tracing for Lambda"
  type        = bool
  default     = false
}

variable "enable_dlq" {
  description = "Enable Dead Letter Queue for failed Lambda invocations"
  type        = bool
  default     = true
}

variable "enable_lambda_alarms" {
  description = "Enable CloudWatch alarms for Lambda errors and duration"
  type        = bool
  default     = true
}

