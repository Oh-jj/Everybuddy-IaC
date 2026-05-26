# ============================================================
# Provider
# ============================================================
provider "aws" {
  profile = "default"
  region  = var.aws_region
}

# ============================================================
# Networking
# ============================================================
module "networking" {
  source       = "./modules/networking"
  project_name = var.project_name
  aws_region   = var.aws_region

  public_subnets = {
    backend    = { cidr = "10.0.1.0/24", az = "ap-southeast-1a" }
    monitoring = { cidr = "10.0.2.0/24", az = "ap-southeast-1a" }
    b          = { cidr = "10.0.3.0/24", az = "ap-southeast-1b" }
  }

  private_app_subnets = {
    a = { cidr = "10.0.11.0/24", az = "ap-southeast-1a" }
    b = { cidr = "10.0.12.0/24", az = "ap-southeast-1b" }
  }

  private_db_subnets = {
    a = { cidr = "10.0.21.0/24", az = "ap-southeast-1a" }
    b = { cidr = "10.0.22.0/24", az = "ap-southeast-1b" }
  }
}

# ============================================================
# Security Groups
# ============================================================
module "security" {
  source       = "./modules/security"
  project_name = var.project_name
  vpc_id       = module.networking.vpc_id

  gpu_server_cidrs = var.gpu_server_cidrs
}

# ============================================================
# Compute
# EC2 Instances, Key Pair, Elastic IP
# ============================================================
module "compute" {
  source       = "./modules/compute"
  project_name = var.project_name

  public_key = file("${path.root}/serverkey.pub")

  monitoring_subnet_id = module.networking.public_subnet_ids["monitoring"]
  monitoring_sg_id     = module.security.monitoring_sg_id
  bastion_subnet_id    = module.networking.public_subnet_ids["b"]
  bastion_sg_id        = module.security.bastion_sg_id

  # Private Backend (4단계)
  private_backend_subnet_id = module.networking.private_app_subnet_ids["a"]
  private_backend_sg_id     = module.security.private_backend_sg_id

  backend_instance_type    = var.backend_instance_type
  monitoring_instance_type = var.monitoring_instance_type
  bastion_instance_type    = var.bastion_instance_type
}

# ============================================================
# Storage
# S3 Bucket (file storage)
# ============================================================
module "storage" {
  source       = "./modules/storage"
  project_name = var.project_name
  environment  = var.environment
  bucket_name  = var.files_bucket_name
}

# ============================================================
# DNS
# Route53 Hosted Zone (everybuddy.cloud)
# ============================================================
module "dns" {
  source       = "./modules/dns"
  project_name = var.project_name
  domain_name  = var.domain_name
}

# ============================================================
# ALB
# Application Load Balancer + ACM + Route53 A record
# ============================================================
module "alb" {
  source       = "./modules/alb"
  project_name = var.project_name
  vpc_id       = module.networking.vpc_id

  public_subnet_ids = [
    module.networking.public_subnet_ids["backend"],
    module.networking.public_subnet_ids["b"],
  ]

  domain_name   = var.domain_name
  zone_id       = module.dns.zone_id
  backend_sg_id = module.security.backend_sg_id
}

# ALB Target Group — Private Backend EC2 등록
resource "aws_lb_target_group_attachment" "backend" {
  target_group_arn = module.alb.target_group_arn
  target_id        = module.compute.private_backend_instance_id
  port             = 8080
}

# ALB SG → Private Backend EC2 :8080 허용
resource "aws_security_group_rule" "private_backend_from_alb" {
  type                     = "ingress"
  description              = "Spring Boot from ALB"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = module.alb.alb_sg_id
  security_group_id        = module.security.private_backend_sg_id
}

# ============================================================
# Database
# RDS MySQL (Private DB Subnet)
# ============================================================
module "database" {
  source       = "./modules/database"
  project_name = var.project_name
  vpc_id       = module.networking.vpc_id

  private_db_subnet_ids = module.networking.private_db_subnet_ids

  # map 형태로 전달 (for_each 키가 정적이라 plan/apply 에러 없음)
  allowed_sg_ids = {
    backend         = module.security.backend_sg_id
    private_backend = module.security.private_backend_sg_id
    bastion         = module.security.bastion_sg_id
  }

  db_name           = var.db_name
  db_username       = var.db_username
  db_password       = var.db_password
  db_instance_class = var.db_instance_class
}

# ============================================================
# WAF
# AWS WAF v2 - ALB 연결 (관리형 규칙, OWASP Top 10)
# ============================================================
module "waf" {
  source       = "./modules/waf"
  project_name = var.project_name
  alb_arn      = module.alb.alb_arn
}

# ============================================================
# Bedrock Agent
# laurel GPU 서버 이상 감지 → Bedrock 분석 → Slack 알림
# ============================================================
module "bedrock_agent" {
  source       = "./modules/bedrock_agent"
  project_name = var.project_name
  aws_region   = var.aws_region

  slack_webhook_url     = var.slack_webhook_url
  existing_role_arn     = var.existing_role_arn
  prometheus_private_ip = module.compute.monitoring_private_ip

  # private-app-a 서브넷 (NAT GW 연결 — Bedrock/Slack outbound 가능)
  private_subnet_ids = [module.networking.private_app_subnet_ids["a"]]
  lambda_sg_id       = module.security.lambda_gpu_monitor_sg_id
}
