variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "aws_region" {
  description = "AWS region (S3 VPC Endpoint service name에 사용)"
  type        = string
  default     = "ap-southeast-1"
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnets" {
  description = "Public subnets: key = identifier, value = { cidr, az }"
  type = map(object({
    cidr = string
    az   = string
  }))
}

variable "private_app_subnets" {
  description = "Private app subnets (Spring Boot, FastAPI): key = identifier, value = { cidr, az }"
  type = map(object({
    cidr = string
    az   = string
  }))
}

variable "private_db_subnets" {
  description = "Private DB subnets (RDS): key = identifier, value = { cidr, az }"
  type = map(object({
    cidr = string
    az   = string
  }))
}
