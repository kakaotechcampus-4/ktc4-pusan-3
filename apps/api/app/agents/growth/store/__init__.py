"""Growth Agent와 DB·외부 데이터 사이 통로.
구현체는 DB·외부 API 가 연결된 뒤 같은 Protocol로 붙인다. 지금은 포트와 테스트용 InMemory 까지다.
Growth는 아이 데이터를 쓰지 않는다. 기록은 Memory가, 추천 저장은 주입된 writer가 한다.
"""
