# ============================================================
# SSM Parameter Store — Slack Webhook URL (SecureString)
# ============================================================
resource "aws_ssm_parameter" "slack_webhook" {
  name        = "/${var.project_name}/slack/gpu-webhook"
  description = "Slack Incoming Webhook URL for GPU monitoring alerts"
  type        = "SecureString"
  value       = var.slack_webhook_url

  tags = {
    Name = "${var.project_name}-slack-gpu-webhook"
  }
}

# ============================================================
# Lambda 배포 패키지
# ============================================================
data "archive_file" "gpu_monitor" {
  type        = "zip"
  source_file = "${path.root}/lambda_src/gpu_monitor/lambda_function.py"
  output_path = "${path.root}/.lambda_builds/gpu_monitor.zip"
}

# ============================================================
# Lambda Function
# ============================================================
resource "aws_lambda_function" "gpu_monitor" {
  function_name = "${var.project_name}-gpu-monitor"
  description   = "laurel GPU 서버 이상 감지 + Bedrock 분석 + Slack 알림 에이전트"

  filename         = data.archive_file.gpu_monitor.output_path
  source_code_hash = data.archive_file.gpu_monitor.output_base64sha256

  role        = var.existing_role_arn
  handler     = "lambda_function.lambda_handler"
  runtime     = "python3.12"
  timeout     = 60
  memory_size = 256

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.lambda_sg_id]
  }

  environment {
    variables = {
      PROMETHEUS_ENDPOINT = "http://${var.prometheus_private_ip}:9090"
      SLACK_WEBHOOK_SSM   = aws_ssm_parameter.slack_webhook.name
      BEDROCK_MODEL_ID    = "anthropic.claude-3-5-haiku-20241022-v1:0"
      BEDROCK_REGION      = var.aws_region
    }
  }

  tags = {
    Name = "${var.project_name}-gpu-monitor"
  }
}

# ============================================================
# CloudWatch Log Group — 14일 보존
# ============================================================
resource "aws_cloudwatch_log_group" "gpu_monitor" {
  name              = "/aws/lambda/${aws_lambda_function.gpu_monitor.function_name}"
  retention_in_days = 14

  tags = {
    Name = "${var.project_name}-gpu-monitor-logs"
  }
}

# ============================================================
# EventBridge — 2분 주기 스케줄
# ============================================================
resource "aws_cloudwatch_event_rule" "gpu_monitor" {
  name                = "${var.project_name}-gpu-monitor-schedule"
  description         = "laurel GPU monitoring Lambda trigger every 2 minutes"
  schedule_expression = "rate(2 minutes)"

  tags = {
    Name = "${var.project_name}-gpu-monitor-schedule"
  }
}

resource "aws_cloudwatch_event_target" "gpu_monitor" {
  rule      = aws_cloudwatch_event_rule.gpu_monitor.name
  target_id = "GpuMonitorLambda"
  arn       = aws_lambda_function.gpu_monitor.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gpu_monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gpu_monitor.arn
}
