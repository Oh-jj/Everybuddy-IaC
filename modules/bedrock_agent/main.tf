# ============================================================
# SSM Parameter Store — Slack Webhook URL (SecureString)
# git에 절대 저장하지 않고 AWS SSM에 암호화 저장
# ============================================================
resource "aws_ssm_parameter" "slack_webhook" {
  name        = "/${var.project_name}/slack/gpu-webhook"
  description = "Slack Incoming Webhook URL — GPU 모니터링 알림용"
  type        = "SecureString"
  value       = var.slack_webhook_url

  tags = {
    Name = "${var.project_name}-slack-gpu-webhook"
  }
}

# ============================================================
# IAM Role — Lambda 실행 역할
# ============================================================
resource "aws_iam_role" "gpu_monitor" {
  name = "${var.project_name}-gpu-monitor-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project_name}-gpu-monitor-role"
  }
}

# Lambda VPC 실행 + CloudWatch Logs 권한 (AWS 관리형 정책)
resource "aws_iam_role_policy_attachment" "vpc_execution" {
  role       = aws_iam_role.gpu_monitor.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Bedrock InvokeModel + SSM GetParameter 최소 권한
resource "aws_iam_role_policy" "gpu_monitor" {
  name = "${var.project_name}-gpu-monitor-policy"
  role = aws_iam_role.gpu_monitor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0"
        ]
      },
      {
        Sid      = "SSMGetWebhook"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [aws_ssm_parameter.slack_webhook.arn]
      }
    ]
  })
}

# ============================================================
# Lambda 배포 패키지 — archive_file로 Python 소스 zip
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

  role        = aws_iam_role.gpu_monitor.arn
  handler     = "lambda_function.lambda_handler"
  runtime     = "python3.12"
  timeout     = 60     # Prometheus 조회 + Bedrock 호출 여유 있게
  memory_size = 256

  # VPC 내부 배치 → Prometheus private IP 접근 + NAT GW 경유 Bedrock/Slack 호출
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

  depends_on = [
    aws_iam_role_policy_attachment.vpc_execution,
    aws_iam_role_policy.gpu_monitor,
  ]
}

# ============================================================
# CloudWatch Log Group — 14일 보존 (Lambda 자동 생성 전에 선언)
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
  description         = "laurel GPU 모니터링 Lambda 2분 주기 트리거"
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
