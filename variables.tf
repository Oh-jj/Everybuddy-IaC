variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "everybuddy"
}

variable "environment" {
  description = "Environment (prod, staging, dev)"
  type        = string
  default     = "prod"
}

variable "backend_instance_type" {
  description = "EC2 instance type for backend server"
  type        = string
  default     = "t3.medium"
}

variable "monitoring_instance_type" {
  description = "EC2 instance type for monitoring server"
  type        = string
  default     = "t3.micro"
}

variable "bastion_instance_type" {
  description = "EC2 instance type for Bastion server"
  type        = string
  default     = "t3.micro"
}

variable "domain_name" {
  description = "Root domain name"
  type        = string
  default     = "everybuddy.cloud"
}

variable "files_bucket_name" {
  description = "S3 bucket name for file storage"
  type        = string
  default     = "everybuddy-files-prod-20250103"
}

# ── RDS ─────────────────────────────────────────────────────
variable "db_name" {
  description = "Database name"
  type        = string
  default     = "everybuddy"
}

variable "db_username" {
  description = "RDS master username"
  type        = string
  default     = "admin"
}

# 비밀번호는 tfvars에 저장하지 않음
# 실행 전 환경변수로 주입: export TF_VAR_db_password="your_password"
variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "gpu_server_cidrs" {
  description = "External GPU server CIDRs allowed to push logs to Loki (port 3100)"
  type        = list(string)
  default     = ["112.220.79.222/32"]
}

# ── Bedrock Agent ────────────────────────────────────────────
# 비밀번호와 동일하게 환경변수로 주입
# 실행 전: export TF_VAR_slack_webhook_url="https://hooks.slack.com/services/..."
variable "slack_webhook_url" {
  description = "Slack Incoming Webhook URL (GPU 모니터링 알림 — git에 절대 저장 금지)"
  type        = string
  sensitive   = true
}

variable "existing_role_arn" {
  description = "관리자가 사전 생성한 Lambda 실행 IAM Role ARN"
  type        = string
  # 실행 전: export TF_VAR_existing_role_arn="arn:aws:iam::<ACCOUNT_ID>:role/everybuddy-gpu-monitor-role"
}

variable "datalake_bucket_suffix" {
  description = "Data Lake S3 버킷명 중복 방지용 suffix (예: 계정ID 앞 6자리)"
  type        = string
  # 실행 전: export TF_VAR_datalake_bucket_suffix="<고유값>"
}
