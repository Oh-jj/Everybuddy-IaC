# ============================================================
# Backend Security Group
# ============================================================
resource "aws_security_group" "backend" {
  name        = "${var.project_name}-backend-sg"
  description = "Security group for backend server"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-backend-sg"
  }
}

resource "aws_security_group_rule" "backend_ssh" {
  type              = "ingress"
  description       = "SSH access"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.backend.id
}

resource "aws_security_group_rule" "backend_http" {
  type              = "ingress"
  description       = "HTTP access"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.backend.id
}

resource "aws_security_group_rule" "backend_https" {
  type              = "ingress"
  description       = "HTTPS access"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.backend.id
}

resource "aws_security_group_rule" "backend_app" {
  type              = "ingress"
  description       = "Spring Boot application"
  from_port         = 8080
  to_port           = 8080
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.backend.id
}

resource "aws_security_group_rule" "backend_node_exporter" {
  type                     = "ingress"
  description              = "Node Exporter metrics for Prometheus"
  from_port                = 9100
  to_port                  = 9100
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.monitoring.id
  security_group_id        = aws_security_group.backend.id
}

resource "aws_security_group_rule" "backend_actuator_prometheus" {
  type                     = "ingress"
  description              = "Spring Boot Actuator metrics for Prometheus"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.monitoring.id
  security_group_id        = aws_security_group.backend.id
}

resource "aws_security_group_rule" "backend_egress" {
  type              = "egress"
  description       = "Allow all outbound traffic"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.backend.id
}

# ============================================================
# Monitoring Security Group
# ============================================================
resource "aws_security_group" "monitoring" {
  name        = "${var.project_name}-monitoring-sg"
  description = "Security group for monitoring server"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-monitoring-sg"
  }
}

resource "aws_security_group_rule" "monitoring_ssh" {
  type              = "ingress"
  description       = "SSH access"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.monitoring.id
}

resource "aws_security_group_rule" "monitoring_grafana" {
  type              = "ingress"
  description       = "Grafana web UI"
  from_port         = 3000
  to_port           = 3000
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.monitoring.id
}

resource "aws_security_group_rule" "monitoring_prometheus" {
  type              = "ingress"
  description       = "Prometheus web UI"
  from_port         = 9090
  to_port           = 9090
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.monitoring.id
}

resource "aws_security_group_rule" "monitoring_loki" {
  type                     = "ingress"
  description              = "Loki push endpoint from backend"
  from_port                = 3100
  to_port                  = 3100
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.backend.id
  security_group_id        = aws_security_group.monitoring.id
}

resource "aws_security_group_rule" "monitoring_loki_from_gpu" {
  count             = length(var.gpu_server_cidrs) > 0 ? 1 : 0
  type              = "ingress"
  description       = "Loki push endpoint from external GPU server"
  from_port         = 3100
  to_port           = 3100
  protocol          = "tcp"
  cidr_blocks       = var.gpu_server_cidrs
  security_group_id = aws_security_group.monitoring.id
}

resource "aws_security_group_rule" "monitoring_egress" {
  type              = "egress"
  description       = "Allow all outbound traffic"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.monitoring.id
}

# ============================================================
# Bastion Security Group
# ============================================================
resource "aws_security_group" "bastion" {
  name        = "${var.project_name}-bastion-sg"
  description = "Security group for Bastion server"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-bastion-sg"
  }
}

resource "aws_security_group_rule" "bastion_ssh" {
  type              = "ingress"
  description       = "SSH access from anywhere (restrict to your IP in production)"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.bastion.id
}

resource "aws_security_group_rule" "bastion_egress" {
  type              = "egress"
  description       = "Allow all outbound traffic"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.bastion.id
}

# ============================================================
# Bastion → Backend/Monitoring SSH (Private 전환 대비)
# 4단계에서 backend SSH 0.0.0.0/0 규칙 삭제 후 이 규칙으로 접근
# ============================================================
resource "aws_security_group_rule" "backend_ssh_from_bastion" {
  type                     = "ingress"
  description              = "SSH from Bastion"
  from_port                = 22
  to_port                  = 22
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.bastion.id
  security_group_id        = aws_security_group.backend.id
}

resource "aws_security_group_rule" "monitoring_ssh_from_bastion" {
  type                     = "ingress"
  description              = "SSH from Bastion"
  from_port                = 22
  to_port                  = 22
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.bastion.id
  security_group_id        = aws_security_group.monitoring.id
}

# ============================================================
# Private Backend Security Group
# 8080: ALB SG에서만 (root main.tf에서 규칙 추가)
# 22:   Bastion SG에서만
# ============================================================
resource "aws_security_group" "private_backend" {
  name        = "${var.project_name}-private-backend-sg"
  description = "Security group for Private Backend EC2"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-private-backend-sg"
  }
}

resource "aws_security_group_rule" "private_backend_ssh_from_bastion" {
  type                     = "ingress"
  description              = "SSH from Bastion"
  from_port                = 22
  to_port                  = 22
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.bastion.id
  security_group_id        = aws_security_group.private_backend.id
}

resource "aws_security_group_rule" "private_backend_egress" {
  type              = "egress"
  description       = "Allow all outbound traffic"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.private_backend.id
}

# Monitoring → Private Backend: Node Exporter (9100)
resource "aws_security_group_rule" "private_backend_node_exporter" {
  type                     = "ingress"
  description              = "Node Exporter metrics for Prometheus"
  from_port                = 9100
  to_port                  = 9100
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.monitoring.id
  security_group_id        = aws_security_group.private_backend.id
}

# Monitoring → Private Backend: Spring Boot Actuator (8080)
resource "aws_security_group_rule" "private_backend_actuator_prometheus" {
  type                     = "ingress"
  description              = "Spring Boot Actuator metrics for Prometheus"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.monitoring.id
  security_group_id        = aws_security_group.private_backend.id
}

# Private Backend → Monitoring: Loki 로그 푸시 (3100)
resource "aws_security_group_rule" "private_backend_to_loki" {
  type                     = "ingress"
  description              = "Loki push endpoint from private backend"
  from_port                = 3100
  to_port                  = 3100
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.private_backend.id
  security_group_id        = aws_security_group.monitoring.id
}
