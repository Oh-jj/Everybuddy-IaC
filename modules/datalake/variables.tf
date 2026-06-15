variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "suffix" {
  description = "버킷명 중복 방지용 suffix (예: 계정ID 앞 6자리 또는 날짜)"
  type        = string
}

variable "lambda_role_arn" {
  description = "S3 PutObject를 허용할 Lambda 실행 IAM Role ARN"
  type        = string
}
