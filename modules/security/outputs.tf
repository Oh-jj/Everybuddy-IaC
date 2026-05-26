output "backend_sg_id" {
  description = "Backend security group ID"
  value       = aws_security_group.backend.id
}

output "monitoring_sg_id" {
  description = "Monitoring security group ID"
  value       = aws_security_group.monitoring.id
}

output "bastion_sg_id" {
  description = "Bastion security group ID"
  value       = aws_security_group.bastion.id
}

output "private_backend_sg_id" {
  description = "Private Backend EC2 security group ID"
  value       = aws_security_group.private_backend.id
}

output "lambda_gpu_monitor_sg_id" {
  description = "Lambda GPU 모니터링 에이전트 security group ID"
  value       = aws_security_group.lambda_gpu_monitor.id
}
