# terraform.tfvars
aws_region   = "us-west-1"
environment  = "prod"
alert_emails = ["ameercheguvera007@gmail.com"]

# Optional (defaults are fine if not specified)
scan_schedule            = "cron(0 2 * * ? *)"
lambda_log_retention_days = 30
enable_pitr              = true

# This is used for GitHub OIDC authentication
github_repository = "ameer-sk1401/CostGuardian_terraform"