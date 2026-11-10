"""삭제 이력의 보관 기간이 처리방침과 같은지 (#758).

처리방침이 보호자에게 "35일" 이라고 알리고, 정리 배치가 그 기간으로 지운다. 한쪽만 바뀌면 알린 것과
하는 것이 달라진다. 그리고 35일은 가장 오래된 백업(28일)보다 길어야 한다 — 짧으면 그 백업을 복원할
때 필요한 이력이 먼저 사라진다.
"""

from datetime import timedelta

from app.workers.deletion_log_purge import RETENTION
from scripts.render_privacy_policy import PRIVACY

# 백업 버킷의 S3 수명 주기(28일 만료)를 옮겨 적은 값이다 — 콘솔 설정이라 코드에서 읽을 수 없다.
# 수명 주기를 바꾸면 여기와 deploy/scripts/backup.sh 머리말을 같이 고친다 (#758).
OLDEST_BACKUP = timedelta(days=28)


def test_처리방침의_삭제_이력_보관_기간이_정리_배치와_같다():
    newest = max(PRIVACY.glob("*.md"), key=lambda p: p.stem).read_text(encoding="utf-8")
    [row] = [line for line in newest.splitlines() if line.startswith("| 삭제 이력")]

    assert f"{RETENTION.days}일이 지나면 하루 한 번 서버에서 지웁니다" in row
    assert RETENTION > OLDEST_BACKUP
