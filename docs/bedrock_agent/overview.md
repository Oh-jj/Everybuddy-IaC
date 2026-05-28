# laurel GPU 서버 모니터링 에이전트 (Bedrock Agent)

**설계 시작:** 2026-05  
**담당 브랜치:** `7-feat-laurel-server-ai-agent-aws-bedrock`  
**배포 방식:** AWS Lambda (Terraform 관리)

---

## 1. 설계 의도

laurel은 외부 온프레미스 GPU 서버로 everybuddy의 AI 번역 추론을 담당한다.  
NVIDIA Triton Inference Server가 Docker 컨테이너로 실행되며, 모델 로드 실패나 컨테이너 재시작 루프 같은 장애가 발생해도 **사람이 직접 SSH 접속해 확인하기 전까지 인지할 방법이 없었다.**

이 에이전트는 다음 목표로 설계됐다:

- GPU 메트릭(온도·사용률·VRAM) 이상을 자동 감지
- Triton 컨테이너 에러 로그를 실시간 수집
- AWS Bedrock(Claude)이 데이터를 직접 분석해 원인과 조치를 Slack으로 전달
- 반복 알림 억제로 Bedrock 토큰 낭비 방지

---

## 2. 전체 아키텍처

```
EventBridge 스케줄 (rate 2분) ← cron 방식, 이벤트 기반 아님
        │  2분마다 Lambda를 강제 호출
        ▼
  AWS Lambda (VPC 내부, private-app-a 서브넷)
        │
        │  ← VPC 내부 직접 통신 (Private IP)
        ├─── Monitoring EC2 (VPC 내부)
        │       ├─ Prometheus :9090  ← laurel DCGM·Node Exporter 스크래핑
        │       └─ Loki       :3100  ← laurel Promtail이 로그 push
        │
        │  ← NAT Gateway 경유 (Bedrock VPC Endpoint 미구성)
        ├─── AWS Bedrock (Claude 3.5 Sonnet, ap-southeast-1)
        │
        └─── Slack Incoming Webhook

laurel (외부 온프레미스 GPU 서버)
  ├─ DCGM Exporter, Node Exporter 실행 → Prometheus가 스크래핑
  └─ Promtail 실행 → Triton 로그를 Loki로 push
```

**Lambda는 laurel과 직접 통신하지 않는다.**  
laurel의 메트릭과 로그는 Monitoring EC2(VPC 내부)에 이미 수집되어 있으며, Lambda는 Monitoring EC2에만 질의한다.

**EventBridge는 스케줄 기반 트리거다.**  
2분마다 Lambda를 깨우고, Lambda가 능동적으로 Prometheus·Loki에 HTTP 폴링한다.  
laurel이 직접 이벤트를 발행하는 구조가 아니다.

**Bedrock은 NAT Gateway를 통해 접근한다.**  
Bedrock VPC Interface Endpoint(PrivateLink)가 구성되어 있지 않아 Lambda → NAT GW → Bedrock 퍼블릭 엔드포인트 경로를 사용한다.

---

## 3. 데이터 수집 설계

### 3-1. Prometheus 메트릭

| 메트릭 | 쿼리 방식 | 용도 |
|---|---|---|
| GPU 온도 | range (10분) | 현재값 + 상승 트렌드 계산 |
| GPU 사용률 | instant | 현재 부하 확인 |
| VRAM 사용량 | instant + range | 현재 사용률 + 급락 감지 |
| GPU 전력 | instant | 이상 부하 참고 |
| 시스템 메모리 | instant | 호스트 메모리 압박 감지 |

**VRAM 급락 감지:**  
range 쿼리로 10분 내 10GB 이상 감소 시 Triton 모델 크래시 또는 컨테이너 재시작으로 판단한다.  
정상 VRAM: 모델 로드 시 GPU당 약 15~16 GB / 미로드 시 약 400 MB.

### 3-2. Loki 에러 로그

Triton 컨테이너 로그(`service_name="triton"`)에서 2분간 아래 패턴 매칭:

```
error | critical | fatal | exception | oom | killed | traceback |
panic | cuda | segfault | failed to load | failed to create |
server.*failed | restarting | failed
```

백엔드·기타 컨테이너 로그는 의도적으로 제외한다.

---

## 4. 이상 감지 임계값

| 항목 | WARNING | CRITICAL |
|---|---|---|
| GPU 온도 | 75°C 이상 | 82°C 이상 |
| GPU 사용률 | 85% 이상 | 95% 이상 |
| VRAM 사용률 | 80% 이상 | 90% 이상 |
| 시스템 메모리 | 85% 이상 | — |
| GPU 온도 상승속도 | 1.5°C/분 이상 | — |
| VRAM 급락 | — | 10분 내 10GB↓ |

**Triton 포트(8002) 메트릭은 트리거에서 제외한다.**  
네트워크 팀의 포트 개방 전까지 수집이 불가능하므로, 오탐 방지를 위해 의도적으로 제거됐다.  
포트가 열리면 `collect()`에서 자동 수집되어 Bedrock 프롬프트 컨텍스트로 활용된다.

---

## 5. 알림 흐름

```
collect()  →  detect()  →  query_loki_errors()
                │
          ┌─────┴──────┐
          │            │
    메트릭 이상?    메트릭 정상?
     (WARNING/       │
      CRITICAL)   Loki 에러 로그 있음?
          │            │
   check_cooldown()   YES → build_log_prompt()
          │                  → call_bedrock()
    억제 중?              → send_slack_log()
     YES → 스킵
      NO → build_metric_prompt()
           → call_bedrock()
           → send_slack_metric()
```

### 5-1. 메트릭 이상 경로 (WARNING / CRITICAL)

- Bedrock에 GPU 메트릭 전체 + Loki 에러 로그 + 서버 인프라 컨텍스트를 함께 전달
- 분석 형식: `[진단] [근거] [조치] [예측]`
- Slack에 심각도별 색상(빨강/주황)과 함께 전송

### 5-2. 에러 로그 단독 경로

- 메트릭은 정상이지만 Triton 로그에 에러 패턴 감지 시 발동
- 로그 기반 소프트웨어 레벨 분석 (설정·드라이버·HuggingFace 로드 실패 등)
- 쿨다운 미적용 — 에러 로그는 매번 새로운 사건으로 간주

---

## 6. 쿨다운 메커니즘

**문제:** 장애가 해소되지 않으면 2분마다 동일한 알림이 반복 전송되어 Bedrock 토큰 및 Slack 채널을 낭비한다.

**해결:** Lambda 전역변수를 이용한 Warm Start 기반 쿨다운

```python
COOLDOWN_MINUTES = 30
_last_alert_hash: str | None = None  # 이슈 목록 MD5 해시
_last_alert_time: float | None = None
```

동작 방식:
1. 이슈 목록을 정렬 후 MD5 해시로 fingerprint 생성
2. 동일 해시가 30분 이내 재발생 시 Bedrock 호출 및 Slack 전송 스킵
3. 이슈 해소(NORMAL) 시 자동 초기화

**특성 및 제약:**
- Lambda 컨테이너가 살아있는 동안만 유효 (Warm Start 의존)
- 재배포(terraform apply) 시 상태 초기화 → 알림 1회 추가 발생 가능
- IAM PutParameter 권한 확보 시 SSM Parameter Store 방식으로 교체 예정 (100% 신뢰)
- 2분 주기 실행 환경에서 컨테이너가 회수될 가능성은 낮아 실용적으로 충분

---

## 7. Bedrock 프롬프트 설계

### 메트릭 이상 프롬프트 컨텍스트

Bedrock이 일반적인 수준의 분석을 내놓는 문제를 해결하기 위해 아래 정보를 프롬프트에 주입한다:

- GPU별 모델 할당 (GPU 0→gemma_s2tt, 1→gemma_v2tt, 2,3→gemma_t2tt, CPU→Supertonic_tts)
- 정상 VRAM 범위 (로드 시 15~16 GB / 유휴 시 ~400 MB)
- Triton 실행 커맨드, 재시작 정책, 헬스체크 설정
- `strict_readiness=true` 동작 (모델 하나 실패 시 전체 재시작)
- 드라이버 535 vs 요구 560 호환성 경고 (정상 출력, 무시 가능)
- 알려진 이슈 목록 (HuggingFace 다운로드 실패, shm 부족 등)

### 에러 로그 프롬프트

이미 수집된 로그를 Bedrock이 직접 읽고 분석하도록 강제한다.  
"로그를 확인하세요" 같은 재확인 지시를 명시적으로 금지한다.

---

## 8. GPU 서버 환경 (참고)

| 항목 | 내용 |
|---|---|
| 서버명 | laurel (외부 온프레미스) |
| GPU | NVIDIA Tesla V100-DGXS-32GB × 4 |
| Triton 이미지 | nvcr.io/nvidia/tritonserver:24.12-py3 (CUDA 12.6) |
| PyTorch | 2.6.0+cu118 (드라이버 535와 호환) |
| 재시작 정책 | unless-stopped |
| 헬스체크 | 30s 간격, start_period=120s, retries=5 |

---

## 9. 비용 구조

| 항목 | 단가 | 예상 월 비용 |
|---|---|---|
| Lambda | 2분 주기 × 30일 = 21,600회, 60초 256MB | ~$0.05 |
| Bedrock (Claude 3.5 Sonnet) | 이상 감지 시만 호출, 쿨다운 30분 적용 | 이상 없으면 $0, 지속 장애 시 ~$1~2 |
| SSM GetParameter | 월 10,000회 무료 | $0 |
| CloudWatch Logs | 14일 보존 | ~$0.01 |

정상 운영 시 Bedrock 호출이 거의 없어 **월 $0.1 미만** 예상.

---

## 10. 향후 개선 방향

- [ ] IAM PutParameter 권한 확보 후 쿨다운을 SSM 방식으로 교체
- [ ] Triton 포트(8002) 개방 시 추론 요청수·큐 대기시간 트리거 추가
- [ ] VRAM 급락 감지 시 연관 Loki 컨텍스트 로그 함께 수집 (query_loki_context 재도입)
- [ ] Bedrock max_tokens 600 → 1000 상향 (더 상세한 분석)
