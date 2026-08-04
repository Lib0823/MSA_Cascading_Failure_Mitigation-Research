# GNN 연구를 위한 학습 커리큘럼

> 이 문서는 `research/study-notes/`의 커리큘럼(무엇을, 왜, 어떤 순서로 공부하는지) 문서다. 각 STEP을 공부하며 정리한 학습 노트 원본(PDF)은 같은 폴더에 있다.
>
> 대상: 자바 개발자 + AI·SW 대학원 재학 기준. ANN/DNN → CNN(개념) → RNN/LSTM → GNN → 신뢰도 산출 방법론 순서로 구성. 다음 학기 딥러닝/소프트웨어공학 수업과 STEP 1~4는 상당 부분 겹칠 수 있어 진도에 맞춰 압축 가능.

## STEP 1. ANN/DNN 기초 + 역전파 (1주)

- **[Must Have]** 텐초의 파이토치 딥러닝 특강 — 1단계: 딥러닝 입문 ([YouTube 재생목록](https://youtube.com/playlist?list=PLgD4RfwkG2A4JqDZGx9YFrQhGz9EL2xO1))
- 골든래빗 출판사의 동명 책과 짝을 이루는 무료 공식 강의. "1단계: 딥러닝 입문"(1장 딥러닝 한눈에 알아보기 → 2장 인공 신경망 ANN 이해하기 → 3장 간단한 신경망 만들기) 순서로 시청.
- 이 시리즈는 STEP 1·2·3·4에 걸쳐 이어서 사용.
- 학습 노트: [1. ANN:DNN 기초 + 역전파.pdf](1.%20ANN%3ADNN%20기초%20%2B%20역전파.pdf)

## STEP 2. Python/PyTorch 실습 (STEP 1과 병행, 1주)

- **[Must Have]** 같은 재생목록의 "3장. 간단한 신경망 만들기" — PyTorch 기본 문법(텐서, nn.Module, 학습 루프) 실습.
- 목표: STEP 1의 이론을 코드로 직접 짜서 간단한 분류기 학습시켜보기.

## STEP 3. CNN 개념 (2~3일, 가볍게)

- **[Must Have]** 같은 재생목록의 "2단계: 4장. 사진 분류하기 (CNN과 VGG)".
- 목표: 깊게 팔 필요 없음 — 합성곱/풀링의 핵심 아이디어만 이해하고 넘어가기.
- 학습 노트: [2. CNN 개념.pdf](2.%20CNN%20개념.pdf)

## STEP 4. RNN/LSTM (1주) — CNN보다 우선순위 높음

- **[Must Have]** 같은 재생목록의 "2단계: 6장. 넷플릭스 주가 예측하기 (RNN으로 첫 시계열 학습)".
- 목표: 시퀀스 처리, hidden state 개념 이해 → [docs/proposal.md](../../docs/proposal.md) §4-3의 LSTM baseline을 직접 구현할 수 있는 수준.
- 학습 노트: [3. RNN:LSTM.pdf](3.%20RNN%3ALSTM.pdf)

> 참고: STEP 1~4는 모두 "텐초의 파이토치 딥러닝 특강" 한 재생목록에서 순서대로 이어지는 무료 공식 강의(1단계 → 2단계 순)이며, 골든래빗 출판사의 동명 도서와 짝을 이루는 검증된 자료.

## STEP 5. GNN 이론 — 연구의 핵심 (4~6주)

- 딥러닝 홀로서기(2019 KAIST 대학원생 세미나, Idea Factory KAIST) — Basic of Graph Convolution Network: https://www.youtube.com/watch?v=YL1jGgcY78U
- KAIST 대학원생들이 직접 만든 세미나로, 국내 개발자 커뮤니티에서 신뢰도 높게 언급되는 자료. GNN 범주 중 가장 기본이 되는 GCN(Graph Convolutional Network)을 한국어로 다룬다.
- GCN 개념을 먼저 잡은 뒤 GraphSAGE·GAT 등으로 확장하려면 [Stanford CS224W 재생목록](https://www.youtube.com/playlist?list=PLoROMvodv4rOP-ImU-O1rYRg2RFxomvFp)을 이어서 볼 것을 권장(한국어 자료가 마땅치 않아 영어 강의로 보완 필요).
- 목표: GRAF·FIRM·AGQ 논문의 GNN 파트를 "노드가 뭐고 엣지가 뭔지" 스스로 도식화할 수 있는 수준.
- 학습 노트: [4. GNN 이론.pdf](4.%20GNN%20이론.pdf)

## STEP 6. PyTorch Geometric(PyG) 실습 (STEP 5 후반과 병행, 2~3주)

- Pytorch Geometric Tutorial — Introduction to Pytorch geometric (Antonio Longa): https://www.youtube.com/watch?v=JtDgmmQ60x8
- PyG 공식 문서(readthedocs)의 External Resources에도 등재된 시리즈의 1편. 이 채널의 재생목록을 이어서 보면 GAT, 그래프오토인코더 등으로 자연스럽게 확장.
- 목표: Online Boutique의 서비스 호출 그래프를 직접 구성해서 GCN/GraphSAGE/GAT 학습 파이프라인을 짤 수 있는 수준.
- 학습 노트: [5. PyTorch Geometric(PyG).pdf](5.%20PyTorch%20Geometric%28PyG%29.pdf)

## STEP 7. Spatio-temporal GNN 개념 (가볍게, 1주)

- The basics of spatio-temporal graph neural networks: https://www.youtube.com/watch?v=RRMU8kJH60Q
- STGNN(그래프+시계열 결합)의 수학적 배경. STEP 5(GNN)와 STEP 4(LSTM)를 각각 이해한 뒤 보면 "두 개념이 어떻게 합쳐지는지" 자연스럽게 이해됨.
- 목표: AGQ/GraphGRU가 사용한 STGNN 구조를 개념적으로 파악 — 관련연구 비교 서술에 필요한 수준.
- 학습 노트: [6. Spatio-temporal GNN 개념.pdf](6.%20Spatio-temporal%20GNN%20개념.pdf)

## STEP 8. 신뢰도(Confidence) 산출 방법론 (2~3주) — 오픈 이슈와 직결

- Lecture 16: Deep Ensemble and Monte Carlo Dropout: https://www.youtube.com/watch?v=jYjLuFiTpck
- MC Dropout과 Deep Ensemble 두 방법을 함께 비교하는 강의. 원 논문은 Gal & Ghahramani(2016) "Dropout as a Bayesian Approximation".
- 목표: 이 STEP이 끝나면 MC Dropout / Deep Ensemble / Softmax entropy 중 신뢰도 산출 방식을 확정할 수 있어야 함(프로포절 심사 1순위 마일스톤). → [research/challenges.md](../challenges.md) F1에서 Deep Ensemble(N=5)로 확정됨.
- 학습 노트: [7. 신뢰도(Confidence) 산출 방법론.pdf](7.%20신뢰도%28Confidence%29%20산출%20방법론.pdf)

## 전체 타임라인 요약

| 주차 | STEP |
|---|---|
| 1~3주 | STEP 1~2 (ANN/DNN, PyTorch — 수업과 병행 시 단축 가능) |
| 3~4주 | STEP 3 (CNN, 가볍게) |
| 4~5주 | STEP 4 (RNN/LSTM) |
| 5~11주 | STEP 5~6 (GNN 이론 + PyG 실습) — 가장 오래 걸림, 최우선순위 |
| 11~12주 | STEP 7 (Spatio-temporal 개념) |
| 12~15주 | STEP 8 (신뢰도 산출 방법론) — 프로포절 심사 직결, 두 번째 최우선순위 |

> 검증 관련 참고: STEP 1~4는 "텐초의 파이토치 딥러닝 특강"(골든래빗 출판사 공식 무료 강의) 한 재생목록으로 통일했고, STEP 5는 "딥러닝 홀로서기"(KAIST 대학원생 세미나)의 GCN 개별 영상으로 교체했다. 모두 한국어 음성 강의이며 검색으로 실제 존재를 확인했다. STEP 6·7·8(PyG 실습, Spatio-temporal GNN, MC Dropout/Deep Ensemble)은 검증된 한국어 자료를 찾지 못해 기존 영어 영상 그대로 유지했다 — 필요 시 유튜브 자동 번역 자막(설정 → 자막 → 자동 번역 → 한국어) 기능을 활용할 수 있다.
