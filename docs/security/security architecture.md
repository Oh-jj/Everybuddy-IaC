# everybuddy 인프라 보안 아키텍처

**프로젝트:** everybuddy  
**환경:** AWS ap-southeast-1 (Singapore)  
**최종 업데이트:** 2026-05-29

---

## 보안 계층 구조

```
인터넷
  │
  ▼
[WAF]          ← L7 애플리케이션 방어 (관리형 룰 + 커스텀 룰)
  │
  ▼
[ALB]          ← HTTPS 종단, HTTP → HTTPS 리디렉션
  │
  ▼
[Private Subnet] ← 공인 IP 없음, 외부 직접 접근 불가
  │
  ├── Spring Boot EC2
  └── RDS MySQL
```

---

## 1. 네트워크 계층 분리 (VPC)

### 서브넷 설계

| 계층 | 서브넷 | 접근성 |
|------|--------|--------|
| Public | public-backend, public-monitoring, public-b | 인터넷 접근 가능 |
| Private App | private-app-a, private-app-b | 인터넷 직접 접근 불가 |
| Private DB | private-db-a, private-db-b | 인터넷 직접 접근 불가 |

**설계 원칙:**
- Spring Boot 애플리케이션 서버는 Private Subnet에 배치 → 공인 IP 없음
- RDS는 가장 깊은 Private DB Subnet에 배치, 직접 인터넷 접근 불가
- Public Subnet은 ALB, Bastion, Monitoring 서버만 배치

### NAT Gateway

Private Subnet의 아웃바운드 인터넷 트래픽(Docker Hub, Firebase 등)은  
NAT Gateway를 경유. 인바운드는 차단, 아웃바운드만 허용.

### S3 VPC Gateway Endpoint

Private Backend → S3 트래픽은 NAT Gateway를 거치지 않고  
AWS 내부 백본을 통해 직접 통신. 인터넷 노출 없음.

---

## 2. WAF (Web Application Firewall)

ALB에 AWS WAF v2 Regional을 연결하여 L7 수준 방어.

### 룰 구성 (우선순위 순)

| Priority | Rule | 동작 | 설명 |
|----------|------|------|------|
| 0 | RateLimitPerIP | Block | 단일 IP 과다 요청 차단 |
| 1 | AWSManagedRulesAmazonIpReputationList | Block | 악성 IP, 봇, 스캐너 차단 |
| 2 | AllowBinaryUploadEndpoints | Allow | 대용량 업로드 엔드포인트 명시 허용 |
| 3 | AWSManagedRulesCommonRuleSet | Block | OWASP Top 10 방어 |
| 4 | AWSManagedRulesKnownBadInputsRuleSet | Block | PHP injection, Log4j 등 차단 |
| 5 | AWSManagedRulesAnonymousIpList | Block | VPN, 프록시, Tor 출구 노드 차단 |

### AllowBinaryUploadEndpoints 설계 이유

AWS WAF CommonRuleSet은 기본적으로 요청 Body 크기를 제한함.  
음성/영상/이미지 파일 업로드 엔드포인트는 이 제한을 초과하므로  
CommonRuleSet보다 높은 우선순위에서 명시적으로 허용 처리.

---

## 3. Security Groups (최소 권한 원칙)

각 서버는 독립적인 Security Group을 가지며, 필요한 출처에서 필요한 포트만 허용하는 원칙으로 설계.

### Private Backend SG
- 애플리케이션 트래픽: ALB SG 출처만 허용
- 관리 접근(SSH): Bastion SG 출처만 허용
- 메트릭 수집: Monitoring SG 출처만 허용
- 로그 푸시: Monitoring SG 방향으로만 허용

### RDS SG
- DB 접근: 애플리케이션 서버 SG 및 관리 목적 SG 출처만 허용
- 인터넷 직접 접근 완전 차단 (`publicly_accessible = false`)

### Bastion SG
- CI/CD 파이프라인 및 개발자 접근용 Jump Host
- Private EC2 직접 노출 없이 Bastion 경유 접근

### Lambda GPU Monitor SG
- Ingress 불필요 (이벤트 트리거 기반)
- VPC 내부 Prometheus 조회 및 외부 API 호출만 허용

---

## 4. 전송 암호화

### HTTPS (ALB)
- ACM(AWS Certificate Manager)에서 발급한 공인 인증서 적용
- 도메인: `api.everybuddy.cloud`
- ALB Listener HTTP → HTTPS 리디렉션 설정
- 클라이언트 ↔ ALB 구간 TLS 적용

### 내부 통신
- ALB ↔ Private Backend: HTTP (VPC 내부, 네트워크 격리로 대체)
- Private Backend ↔ RDS: Private Subnet 내부 통신

---

## 5. 저장 데이터 암호화

### RDS
```hcl
storage_encrypted = true  # AWS KMS 기본 키로 암호화
```
RDS 볼륨 전체가 AES-256으로 암호화됨.

### S3
버킷 수준 암호화 설정 적용 (SSE-S3).

### SSM Parameter Store
민감 정보는 SSM Parameter Store `SecureString` 타입으로 저장 (KMS 암호화).

---

## 6. 접근 제어

### Bastion (Jump Host) 패턴

외부에서 Private EC2로의 SSH 접근은 반드시 Bastion을 경유:

```
개발자 로컬
  └── SSH ProxyJump → Bastion (Public Subnet)
                          └── SSH → Private Backend (Private Subnet)
```

CI/CD(GitHub Actions)도 동일 패턴 사용.  
Private Backend는 공인 IP가 없어 직접 접근 불가.

### IAM

Lambda 함수는 전용 IAM Role을 사용하며 필요한 최소 권한만 부여:
- Bedrock InvokeModel
- SSM GetParameter
- VPC 네트워크 인터페이스 관리
- CloudWatch Logs 쓰기

---

## 7. 시크릿 관리

| 항목 | 관리 방식 |
|------|-----------|
| RDS 비밀번호 | 환경변수로 주입, tfvars 미포함 |
| Slack Webhook URL | SSM Parameter Store SecureString |
| SSH 키페어 | 로컬 보관, Git 미포함 (`.gitignore` 처리) |
| AWS 자격증명 | AWS CLI Profile, Git 미포함 |
| `.claude/` 디렉토리 | `.gitignore` 처리 (v2.6.1) |

---

## 8. 모니터링 및 가시성

### WAF 모니터링
- CloudWatch Metrics: 룰별 허용/차단 건수
- WAF Sampled Requests: 최근 차단 요청 상세 내역
- 확인 경로: AWS 콘솔 → WAF & Shield → everybuddy-waf → Traffic overview

### 인프라 모니터링
- Prometheus: EC2 Node Exporter, Spring Boot Actuator 메트릭 수집
- Grafana: 대시보드 시각화
- Loki + Promtail: 애플리케이션 로그 중앙 수집

### GPU 서버 모니터링
- DCGM Exporter: GPU 메트릭 수집
- Bedrock 에이전트: 이상 감지 → Claude 분석 → Slack 알림 (v3.1.0~)

---

## 보안 설계 결정 사항

| 결정 | 이유 |
|------|------|
| Spring Boot를 Private Subnet에 배치 | 인터넷 직접 노출 방지, ALB 통해서만 접근 |
| Bastion을 AZ-b에 배치 | 애플리케이션 서버(AZ-a)와 장애 도메인 분리 |
| WAF Allow 룰을 Block 룰보다 앞에 배치 | 정상 업로드 요청이 관리형 룰에 차단되지 않도록 |
| RDS publicly_accessible = false | DB 계층 인터넷 노출 완전 차단 |
| S3 Gateway Endpoint 사용 | S3 트래픽의 인터넷 우회, 비용 절감 겸용 |
| SSM SecureString으로 민감 정보 관리 | 코드/환경변수에 민감 정보 노출 방지 |
