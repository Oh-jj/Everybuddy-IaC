output "monitoring_instance_id" {
  description = "Monitoring EC2 instance ID"
  value       = aws_instance.monitoring.id
}

output "monitoring_public_ip" {
  description = "Monitoring EC2 public IP"
  value       = aws_instance.monitoring.public_ip
}

output "monitoring_private_ip" {
  description = "Monitoring EC2 private IP (VPC 내부 통신용 — Prometheus 엔드포인트)"
  value       = aws_instance.monitoring.private_ip
}

output "monitoring_public_dns" {
  description = "Monitoring EC2 public DNS"
  value       = aws_instance.monitoring.public_dns
}

output "key_pair_name" {
  description = "SSH key pair name"
  value       = aws_key_pair.everybuddy.key_name
}

output "bastion_instance_id" {
  description = "Bastion EC2 instance ID"
  value       = aws_instance.bastion.id
}

output "bastion_public_ip" {
  description = "Bastion EC2 Elastic IP"
  value       = aws_eip.bastion.public_ip
}

output "private_backend_instance_id" {
  description = "Private Backend EC2 instance ID"
  value       = aws_instance.private_backend.id
}

output "private_backend_private_ip" {
  description = "Private Backend EC2 private IP"
  value       = aws_instance.private_backend.private_ip
}
