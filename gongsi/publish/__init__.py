"""게시 단계: posts/ 폴더(깃허브 공개 저장소 = 이미지 호스팅) → 텔레그램 승인 → 인스타그램 캐러셀.

흐름 (GitHub Actions 기준)
  generate.yml  매일 아침  python -m gongsi.pipeline --days 10  →  python -m gongsi.publish stage  →  notify  → git push
  publish.yml   30분마다   python -m gongsi.publish poll   (텔레그램 버튼 응답을 읽어 승인된 것만 인스타그램에 게시) → git push
  token.yml     매주       python -m gongsi.publish refresh-token

상태는 전부 posts/<날짜>/<접수번호>/meta.json 에 있고 저장소에 커밋되므로, 서버 없이 Actions만으로 이어집니다.
"""
