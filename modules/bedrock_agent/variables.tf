variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-1"
}

variable "slack_webhook_url" {
  description = "Slack Incoming Webhook URL (SSM SecureString으로 저장됨 — git 절대 노출 금지)"
  type        = string
  sensitive   = true
}

variable "existing_role_arn" {
  description = "관리자가 사전 생성한 Lambda 실행 IAM Role ARN"
  type        = string
}

variable "prometheus_private_ip" {
  description = "Monitoring EC2 private IP — Lambda가 VPC 내부에서 Prometheus에 접근"
  type        = string
}

variable "private_subnet_ids" {
  description = "Lambda VPC config용 private subnet ID 목록 (NAT GW 경유 outbound 가능한 서브넷)"
  type        = list(string)
}

variable "lambda_sg_id" {
  description = "Lambda 함수용 Security Group ID"
  type        = string
}
