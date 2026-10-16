# T08 맥북 확인

환경: MacBook Pro 14 (Apple M3 Pro, 18GB), macOS 14.6, Python 3.11 (uv), sqlite-vec 로딩 성공. Claude Desktop 연결은 `memory setup desktop` 으로.

## 측정 (scripts/measure_t08.py --existing)

(사용자 맥의 `reports/t08.md` 표를 여기에 붙인다. 컨테이너 참고값은 reports/stage2-4.md.)

## Claude Desktop 대화 확인 (2026-09-18)

대화 1: 가상 노트 3개 저장 요청.

| # | 노트 | saved | indexed | committed | warnings |
| --- | --- | --- | --- | --- | --- |
| 1 | 직장: 노을소프트 (fact, self/employer) | true | true | true | 없음 |
| 2 | 반려동물: 고양이 모찌 (fact, self/pet) | true | true | true | 없음 |
| 3 | 2026년 11월 교토 여행 계획 (episode, 약 430자) | true | true | true | 없음 |

모델이 스스로 `상태/시험데이터` 태그를 붙였고, FACT_CONFLICT·SIMILAR_EXISTS 없음.

대화 2 (새 대화): "내가 다니는 회사랑 고양이 이름 기억나? 교토 숙소 예산은?"

- 세 가지 모두 정답 (노을소프트, 모찌, 1박 3만 엔). 새 대화가 이전 내용을 이어 갔다.
- recall 한 번에 세 건 (유사도 0.63 / 0.53 / 0.48). 교토 노트 0.48 은 임계값 0.45 바로 위다.
- mark_used 신고함.
- 관찰: 처음에는 Desktop 자체 기억을 먼저 떠올린 뒤 도구를 불렀다. 프로젝트 지침(get_core 먼저)을 넣고 도구를 자동 승인으로 바꿨다.
- "200자 뒤 단서를 미리보기만으로 단정하지 않음" 은 노트가 3개뿐이라 전부 전문으로 돌아와 검증되지 않았다. 노트가 4개 이상일 때 다시 본다.

## 판정

통과 (미리보기 항목은 미검증). 유사도 0.48 사례를 min_similarity 재조정 자료로 남긴다.
