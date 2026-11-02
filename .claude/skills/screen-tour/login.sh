#!/usr/bin/env bash
# 로컬 개발 DB 에 로그인 세션 1건을 넣고, 그 refresh 쿠키를 담은 Playwright storageState 를 만든다.
# 카카오 로그인을 자동으로 못 거쳐서 쓰는 개발 전용 우회다. 🚨 로컬 docker DB(ktc4-postgres) 에만 쓴다.
#
#   .claude/skills/screen-tour/login.sh /tmp/state.json [parent_id]
#
# refresh 는 쓸 때마다 바뀐다(회전). 스크립트가 끝날 때 storageState 를 다시 저장하므로 같은 파일을 이어 쓰면 되고,
# 중간에 죽어서 옛 쿠키가 남으면 서버가 재사용으로 보고 그 세션을 끊는다 — 그때는 이걸 다시 돌린다.
# 🚨 다시 돌리면 파일의 localStorage(투어 "봤음") 도 비워진다.
set -euo pipefail
OUT=${1:?storage state path}
PARENT=${2:-$(docker exec ktc4-postgres psql -U dev -d dailyagent -At -c "select id from parent order by created_at limit 1")}
TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
HASH=$(python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$TOKEN")
docker exec ktc4-postgres psql -U dev -d dailyagent -q -c "insert into session(parent_id, refresh_hash, refresh_expires_at, absolute_expires_at) values ('$PARENT', decode('$HASH','hex'), now()+interval '7 days', now()+interval '30 days')"
python3 - "$OUT" "$TOKEN" <<'PY'
import json, sys, time
out, token = sys.argv[1], sys.argv[2]
json.dump({"cookies": [{"name": "icatch_rt", "value": token, "domain": "localhost", "path": "/api/v1/auth",
           "expires": time.time() + 7 * 86400, "httpOnly": True, "secure": True, "sameSite": "Strict"}],
           "origins": []}, open(out, "w"))
PY
echo "parent=$PARENT"
docker exec ktc4-postgres psql -U dev -d dailyagent -At -c "select 'child=' || c.id || ' ' || c.nickname from child c join parent_child pc on pc.child_id = c.id where pc.parent_id = '$PARENT'"
