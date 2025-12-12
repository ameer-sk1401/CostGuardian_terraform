# CostGuardian 🛡️

**Automated AWS Cost Optimization & Resource Management**

CostGuardian is a fully automated AWS cost optimization system that monitors your AWS resources, detects idle or unused services, and safely removes them to reduce your monthly bill. It includes a beautiful real-time dashboard to track savings and deleted resources.

---

## 📊 What Does It Do?

CostGuardian automatically monitors and manages:

- **EC2 Instances** - Detects idle instances (CPU < 5%), stops them, and deletes after grace period
- **RDS Databases** - Monitors connections and IOPS, stops idle databases
- **NAT Gateways** - Identifies unused NAT Gateways with minimal traffic
- **Load Balancers** - Removes ALB/NLB/ELB with no healthy targets
- **S3 Buckets** - Deletes empty buckets and applies lifecycle policies
- **EBS Volumes** - Removes unattached volumes (with snapshot backup)
- **Elastic IPs** - Releases unattached Elastic IPs
- **VPC Resources** - Cleans up empty VPCs and orphaned subnets

### 💰 Cost Savings Dashboard

Real-time web dashboard showing:

- Monthly cost savings
- Resources deleted count
- Historical trends (6 months)
- Service-by-service breakdown
- Cumulative lifetime savings
- Downloadable reports (JSON/CSV)

---

## 🏗️ Architecture

### Components

1. **Main Lambda Function** (`lambda_handler.py`)

   - Runs daily (configurable via EventBridge)
   - Scans AWS account for idle resources
   - Implements multi-stage deletion workflow (warn → quarantine → delete)
   - Creates backups before deletion
   - Sends email alerts via SNS

2. **Cost Savings Lambda** (`cost_savings_calculator.py`)

   - Runs hourly
   - Calculates cost savings from deleted resources
   - Generates dashboard JSON data
   - Archives monthly reports
   - Updates real-time dashboard

3. **S3 Static Website**

   - Beautiful dashboard UI with charts
   - Real-time data from S3 JSON file
   - Auto-refreshes every 5 minutes
   - Download historical reports

4. **DynamoDB Table**

   - Stores resource history and status
   - Tracks deletion timeline
   - Enables historical analysis

5. **S3 Backup Bucket**
   - Stores resource configurations before deletion
   - AMI backups of EC2 instances
   - RDS snapshots
   - Lifecycle policies for archival

### Infrastructure

- **Terraform** for Infrastructure as Code
- **GitHub Actions** for CI/CD (OIDC authentication)
- **CloudWatch Logs** for monitoring
- **SNS** for email notifications
- **EventBridge** for scheduling

---

## ✨ Key Features

### 🎯 Smart Resource Detection

- **CPU Monitoring** - 24-hour CloudWatch metrics analysis
- **Connection Tracking** - Database usage patterns
- **Traffic Analysis** - NAT Gateway and Load Balancer metrics
- **Age-based Rules** - Configurable thresholds per resource type

### 🛡️ Safety First

- **Three-stage workflow**: Warn → Quarantine (Stop) → Delete
- **Configurable grace periods** (default: 1 day after stopping)
- **Automatic backups** before any deletion
- **Protection tags** - Resources with `CostGuardian=Ignore` are skipped
- **Email alerts** at every stage

### 📈 Real-time Dashboard

- Modern, responsive web interface
- Interactive charts (Chart.js)
- Service breakdown with icons
- Historical trends
- Monthly report archives
- One-click downloads

### 🔧 Highly Configurable

All thresholds and behaviors configurable via Lambda environment variables:

- Grace periods
- Idle detection thresholds
- CPU/memory limits
- Monitoring intervals
- Resource-specific rules

---

## 🚀 Getting Started

### Prerequisites

- AWS Account
- Terraform installed (v1.0+)
- AWS CLI configured
- GitHub account (for CI/CD)
- Email address for alerts

### Step 1: Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/costguardian.git
cd costguardian
```

### Step 2: Configure Variables

Edit `terraform.tfvars`:

```hcl
aws_region     = "us-west-1"
environment    = "prod"
alert_email    = "your-email@example.com"
```

### Step 3: Deploy Infrastructure

```bash
# Initialize Terraform
terraform init

# Preview changes
terraform plan

# Deploy
terraform apply
```

This creates:

- 2 Lambda functions
- DynamoDB table
- S3 bucket (backups + dashboard)
- SNS topic (email alerts)
- EventBridge rules (scheduling)
- IAM roles and policies
- CloudWatch log groups
- S3 static website

### Step 4: Confirm SNS Subscription

Check your email and confirm the SNS subscription to receive alerts.

### Step 5: Access Dashboard

After deployment, Terraform outputs the dashboard URL:

```bash
terraform output dashboard_url
```

Open in browser: `http://your-bucket.s3-website-region.amazonaws.com/dashboard/`

### Step 6: Set Up GitHub Actions (Optional)

For automated deployments:

1. **Push to GitHub**:

   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/costguardian.git
   git push -u origin main
   ```

2. **Add GitHub Secret**:

   - Go to Settings → Secrets → Actions
   - Add `AWS_GITHUB_ROLE_ARN` (get from `terraform output`)

3. **Create Production Environment**:

   - Settings → Environments → New environment
   - Name: `production`
   - Enable required reviewers

4. **Deploy**:
   - Infrastructure changes require approval
   - Lambda code updates deploy automatically



---

## 📋 How It Works

### Deletion Workflow

```
Day 1: Resource detected as idle
       ↓
       Status: IDLE_WARNING
       Actions: Backup config, send warning email

Day 2: Still idle
       ↓
       Status: QUARANTINE
       Actions: Stop instance, create AMI, send alert

Day 3: Grace period expires (24 hours after stopping)
       ↓
       Status: DELETED
       Actions: Terminate instance, send confirmation
```

### Dashboard Updates

```
Main Lambda runs (daily)
       ↓
Detects idle resources
       ↓
Deletes resources
       ↓
Logs to DynamoDB

Cost Savings Lambda runs (hourly)
       ↓
Queries DynamoDB
       ↓
Calculates savings
       ↓
Uploads data.json to S3

Dashboard fetches data.json (every 5 min)
       ↓
Updates charts and metrics
```

---

## 🎨 Dashboard Features

### Main Metrics

- **Total Savings This Month** - Current month cost reduction
- **Resources Deleted** - Count of resources removed
- **Cumulative Savings** - Lifetime total savings

### Visualizations

- **Pie Chart** - Savings distribution by service
- **Line Chart** - 6-month historical trend
- **Service Breakdown** - Detailed savings per service type

### Data Tables

- Resource ID and type
- Deletion date
- Instance size
- Monthly savings per resource

### Reports

- **Current Month** - JSON and CSV downloads
- **Historical Reports** - Dropdown to access past months
- **Automatic Archiving** - End-of-month report generation

---

## ⚙️ Configuration

### Lambda Environment Variables

Main Lambda configuration (editable in AWS Console or Terraform):

```
GRACE_PERIOD_DAYS = 1              # Days to wait before deletion
SKIP_QUARANTINE = False            # Skip stopping, delete immediately
IDLE_CHECKS_BEFORE_ACTION = 1      # Consecutive checks before action
CPU_IDLE_THRESHOLD = 5.0           # CPU % to consider idle
```

### Resource-Specific Settings

- **EC2**: CPU threshold, grace period
- **RDS**: Connection threshold, IOPS minimum
- **NAT Gateway**: Traffic threshold (MB)
- **Load Balancers**: Healthy target count
- **S3**: Empty bucket checks
- **EBS**: Create snapshots before deletion

### Protection

Tag any resource with `CostGuardian=Ignore` to exclude from monitoring:

```bash
aws ec2 create-tags \
  --resources i-1234567890abcdef0 \
  --tags Key=CostGuardian,Value=Ignore
```

---

## 📊 Monitoring

### CloudWatch Logs

Two log groups:

- `/aws/lambda/costguardian-prod-main` - Main detection and deletion
- `/aws/lambda/costguardian-prod-cost-savings` - Dashboard updates

### Email Alerts

Receive emails for:

- Idle resource warnings
- Resource quarantined (stopped)
- Resource deleted
- Errors and failures

### Dashboard

Real-time monitoring at: `http://[bucket].s3-website-[region].amazonaws.com/dashboard/`

---

## 💡 Use Cases

### Development/Testing Environments

- Automatically clean up resources after hours
- Reduce costs for non-production workloads
- Prevent orphaned resources

### Cost Optimization

- Identify and remove idle production resources
- Enforce resource lifecycle policies
- Track savings over time

### Compliance & Governance

- Ensure unused resources are removed
- Maintain clean AWS accounts
- Audit resource deletion history

---

## 🔧 Maintenance

### Update Lambda Code

**Local deployment**:

```bash
cd lambda
zip -r ../lambda_function.zip lambda_handler.py requirements.txt
aws lambda update-function-code \
  --function-name costguardian-prod-main \
  --zip-file fileb://lambda_function.zip
```

**GitHub Actions**:

```bash
git add lambda/lambda_handler.py
git commit -m "Update idle detection threshold"
git push origin main
# Automatically deploys!
```

### Update Infrastructure

```bash
vim main.tf
terraform plan
terraform apply
```

### Adjust Configuration

**Option 1: AWS Console**

- Lambda → Functions → Environment variables

**Option 2: Terraform**

- Edit variables in `main.tf` or `terraform.tfvars`
- Run `terraform apply`

### View Logs

```bash
# Main Lambda logs
aws logs tail /aws/lambda/costguardian-prod-main --follow

# Cost Savings Lambda logs
aws logs tail /aws/lambda/costguardian-prod-cost-savings --follow
```

---

## 📁 Project Structure

```
costguardian/
├── main.tf                       # Core infrastructure
├── cost-savings.tf              # Dashboard infrastructure
├── github-oidc.tf               # GitHub Actions auth
├── variables.tf                 # Input variables
├── outputs.tf                   # Output values
├── terraform.tfvars.example     # Example configuration
├── lambda/
│   ├── lambda_handler.py        # Main detection & deletion logic
│   ├── cost_savings_calculator.py  # Dashboard data generator
│   ├── pricing.py               # AWS pricing data
│   └── requirements.txt         # Python dependencies
├── dashboard/
│   ├── index.html               # Dashboard UI
│   └── dashboard.js             # Dashboard logic
├── .github/
│   └── workflows/
│       ├── deploy-lambda.yml    # Lambda deployment
│       ├── deploy-infra.yml     # Infrastructure deployment
│       └── validate.yml         # Code validation
├── README.md                    # This file
├── GITHUB_ACTIONS_GUIDE.md      # CI/CD setup guide
└── DASHBOARD_SETUP.md           # Dashboard configuration
```

---

## 💰 Cost

### Free Tier (First 12 Months)

- Lambda: 1M requests/month, 400,000 GB-seconds
- DynamoDB: 25 GB storage, 25 RCU/WCU
- S3: 5 GB storage
- **CostGuardian Cost**: $0.00/month

### After Free Tier

- Lambda: ~750 invocations/month = ~$0.00
- DynamoDB: Minimal reads/writes = ~$0.00
- S3: ~5 GB storage = ~$0.12/month
- **Total: ~$0.12/month**

### ROI

If CostGuardian deletes just ONE idle `t2.medium` instance:

- **Savings**: $33.87/month
- **Cost**: $0.12/month
- **Net Savings**: $33.75/month
- **ROI**: 28,125%

---

## 🔐 Security

### IAM Permissions

- Least privilege access
- Separate roles for each Lambda
- Read-only CloudWatch access
- Write-only to specific S3 paths

### Data Protection

- Encrypted DynamoDB table
- Encrypted S3 bucket (AES-256)
- Versioned backups
- No credentials in code

### GitHub Actions

- OIDC authentication (no static keys)
- Manual approval for infrastructure changes
- Audit logs for all deployments

---

## 🐛 Troubleshooting

### Dashboard shows $0 savings

**Cause**: No resources deleted yet, or Lambda hasn't run

**Solution**:

```bash
# Manually invoke main Lambda
aws lambda invoke \
  --function-name costguardian-prod-main \
  --payload '{}' response.json

# Wait a moment, then invoke cost savings Lambda
aws lambda invoke \
  --function-name costguardian-prod-cost-savings \
  --payload '{}' response.json

# Check data.json
aws s3 cp s3://[bucket]/dashboard/data.json -
```

### Resources not being deleted

**Cause**: Grace period not expired or protected with tag

**Solution**:

- Check CloudWatch logs for status
- Verify resource doesn't have `CostGuardian=Ignore` tag
- Check DynamoDB for resource status and timestamp
- Adjust `GRACE_PERIOD_DAYS` if needed

### Email alerts not received

**Cause**: SNS subscription not confirmed

**Solution**:

```bash
# Check SNS subscription status
aws sns list-subscriptions-by-topic \
  --topic-arn $(terraform output -raw sns_topic_arn)

# Re-subscribe if needed
aws sns subscribe \
  --topic-arn $(terraform output -raw sns_topic_arn) \
  --protocol email \
  --notification-endpoint your-email@example.com
```

### Dashboard 403 Forbidden

**Cause**: S3 bucket policy not applied or public access blocked

**Solution**:

```bash
# Verify bucket policy
aws s3api get-bucket-policy --bucket [bucket-name]

# Verify public access settings
aws s3api get-public-access-block --bucket [bucket-name]

# Re-apply with Terraform
terraform apply -target=aws_s3_bucket_policy.dashboard_access
```

---

## 🤝 Contributing

We welcome contributions! Here's how:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Test thoroughly
5. Commit (`git commit -m 'Add amazing feature'`)
6. Push to branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/costguardian.git
cd costguardian

# Install dependencies
pip install -r lambda/requirements.txt

# Test Lambda locally
python lambda/lambda_handler.py

# Format code
black lambda/

# Run linter
pylint lambda/
```

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- Built with AWS Lambda, S3, DynamoDB, and EventBridge
- Dashboard powered by Chart.js
- Infrastructure managed with Terraform
- CI/CD via GitHub Actions

---

## 📧 Support

- **Issues**: [GitHub Issues](https://github.com/YOUR_USERNAME/costguardian/issues)
- **Documentation**: See additional guides in repo
- **Email**: your-email@example.com

---

## 🗺️ Roadmap

- [ ] Multi-region support
- [ ] Slack/Teams notifications
- [ ] Custom cost allocation tags
- [ ] Budget alerts integration
- [ ] Machine learning for idle prediction
- [ ] Mobile-responsive dashboard improvements
- [ ] AWS Organizations support
- [ ] Cost anomaly detection

---

## ⭐ Show Your Support

If CostGuardian saves you money, give it a ⭐ on GitHub!

---

**Made with ❤️ for AWS cost optimization**

_Last updated: December 2025_
