variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "gpu_server_cidrs" {
  description = "External GPU server CIDRs allowed to push logs to Loki (port 3100)"
  type        = list(string)
  default     = []
}
