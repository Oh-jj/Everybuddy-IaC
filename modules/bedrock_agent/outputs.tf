output "lambda_function_name" {
  description = "GPU 모니터링 Lambda 함수명"
  value       = aws_lambda_function.gpu_monitor.function_name
}

output "lambda_function_arn" {
  description = "GPU 모니터링 Lambda ARN"
  value       = aws_lambda_function.gpu_monitor.arn
}

output "eventbridge_rule_name" {
  description = "EventBridge 스케줄 규칙명"
  value       = aws_cloudwatch_event_rule.gpu_monitor.name
}

output "log_group_name" {
  description = "CloudWatch 로그 그룹명"
  value       = aws_cloudwatch_log_group.gpu_monitor.name
}
