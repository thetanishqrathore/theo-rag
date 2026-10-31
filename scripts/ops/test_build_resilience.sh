#!/usr/bin/env bash
# End-to-end resilience tests for scripts/build_corpus.py in an ISOLATED scratch
# environment (own database, own Qdrant container, own data dir, small copied
# corpus). Live data is never touched.
#
#   scripts/ops/test_build_resilience.sh
#
# Cases: interrupted build resumes to exact parity without duplicates; edited
# source with a stale manifest is refused (exit 4); changed source is reported as
# retired (exit 5) and replaced with --retire-stale; removed source is retired;
# a deleted canonical file is detected (exit 1) and repaired.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT_DIR/.venv/bin/python"
SRC_CORPUS="${SRC_CORPUS:-/home/tanishq/Documents/theo rag/BIble corpus/bible_corpus}"
WORK="$(mktemp -d)"
DB="theology_rag_buildtest"
QDRANT_NAME="theo_rag_qdrant_buildtest"
QPORT=16334
PSQL=(docker exec -i theo_rag_postgres psql -v ON_ERROR_STOP=1 -U raguser -At)
failures=0

cleanup() {
  "${PSQL[@]}" -d theology_rag -c "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1 || true
  docker rm -f "$QDRANT_NAME" >/dev/null 2>&1 || true
  docker volume rm "${QDRANT_NAME}_data" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

export DATABASE_URL="postgresql://raguser:dev_password@127.0.0.1:5432/$DB"
export QDRANT_PORT=$QPORT
export RAW_DATA_PATH="$WORK/data/raw" CANONICAL_PATH="$WORK/data/canonical" LOG_PATH="$WORK/data/logs"
export PYTHONUNBUFFERED=1 TQDM_DISABLE=1
CORPUS="$WORK/corpus"
MANIFEST="$WORK/data/manifest.json"
REPORTS="$WORK/data/reports"

check() {  # check <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then echo "PASS  $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; failures=$((failures + 1)); fi
}
q() { "${PSQL[@]}" -d "$DB" -c "$1"; }
build() { (cd "$ROOT_DIR" && "$PY" scripts/build_corpus.py --manifest "$MANIFEST" --report-dir "$REPORTS" --tags transcripts,dean_bible_books "$@" >> "$WORK/build.log" 2>&1); }
manifest() { "$PY" "$ROOT_DIR/scripts/source_manifest.py" --root "$CORPUS" --output "$MANIFEST" >/dev/null; }
parity() {  # prints "<db embedded>=<qdrant points>" summed over corpora
  local db qd=0 n
  db="$(q "SELECT count(*) FROM child_chunks WHERE embedding IS NOT NULL")"
  for c in clough_chunks dean_chunks; do
    n="$(curl -fsS "localhost:$QPORT/collections/$c" 2>/dev/null | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])' 2>/dev/null || echo 0)"
    qd=$((qd + n))
  done
  echo "$db=$qd"
}

echo "== setup (scratch db $DB, qdrant :$QPORT, data $WORK)"
mkdir -p "$CORPUS/clough/transcripts/docs" "$CORPUS/dean/transcripts_books_bible/New_Test/1_Cor" "$WORK/data"
for n in 001 002 003 004; do cp "$SRC_CORPUS/clough/transcripts/docs/bf-${n}_transcript.doc" "$CORPUS/clough/transcripts/docs/"; done
for n in 001 002 003 004 006; do cp "$SRC_CORPUS/dean/transcripts_books_bible/New_Test/1_Cor/2002-1Corinthians-$n.htm" "$CORPUS/dean/transcripts_books_bible/New_Test/1_Cor/"; done
"${PSQL[@]}" -d theology_rag -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" >/dev/null
"$PY" "$ROOT_DIR/scripts/migrate.py" --bootstrap --database-url "$DATABASE_URL" | tail -1
docker run -d --name "$QDRANT_NAME" -p "127.0.0.1:$QPORT:6333" -v "${QDRANT_NAME}_data:/qdrant/storage" qdrant/qdrant:v1.7.0 >/dev/null
until curl -fsS "localhost:$QPORT/collections" >/dev/null 2>&1; do sleep 1; done
manifest
sources="$("$PY" -c 'import json,sys; print(sum(e["include"] for e in json.load(open(sys.argv[1]))["entries"]))' "$MANIFEST")"
echo "  $sources sources"

echo "== interrupted build"
# Background the Python process itself (not a subshell), so kill -9 hits the build.
cd "$ROOT_DIR"
"$PY" scripts/build_corpus.py --manifest "$MANIFEST" --report-dir "$REPORTS" --tags transcripts,dean_bible_books >> "$WORK/build.log" 2>&1 &
pid=$!
# Kill once processing is underway (some lessons past ingest, none indexed yet).
for _ in $(seq 1 300); do
  [[ "$(q "SELECT count(*) FROM lessons WHERE status <> 'pending'" 2>/dev/null || echo 0)" -ge 2 ]] && break
  sleep 1
done
kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
pgrep -f "[b]uild_corpus.py --manifest $MANIFEST" >/dev/null && { echo "build still running after kill"; exit 1; }
echo "  killed with lesson statuses: $(q "SELECT string_agg(status || '=' || n, ' ') FROM (SELECT status, count(*) n FROM lessons GROUP BY 1) s")"
build; rc=$?
check "resume after kill -9" 0 "$rc"
check "no duplicate lessons after resume" "$sources" "$(q "SELECT count(*) FROM lessons")"
p="$(parity)"; check "exact parity after resume ($p)" "${p%=*}" "${p#*=}"

echo "== edited source, stale manifest"
edited="$CORPUS/dean/transcripts_books_bible/New_Test/1_Cor/2002-1Corinthians-003.htm"
old_ids="$(q "SELECT string_agg(cc.id::text, ',') FROM child_chunks cc JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id JOIN lessons l ON l.id = pc.lesson_id JOIN source_files sf ON sf.id = l.source_file_id WHERE sf.filename = '2002-1Corinthians-003.htm'")"
sed -i 's#</body>#<p class=MsoNormal>Added paragraph for the resilience test: the rest of faith stands on the promises of God.</p></body>#I' "$edited"
build; check "stale manifest refused" 4 $?

echo "== changed source is retired, then replaced"
manifest
build; check "changed source reported as retired" 5 $?
build --retire-stale; rc=$?
check "retire + rebuild changed source" 0 "$rc"
check "lesson count unchanged after replacement" "$sources" "$(q "SELECT count(*) FROM lessons")"
first_old="${old_ids%%,*}"
left="$(curl -fsS -X POST "localhost:$QPORT/collections/dean_chunks/points" -H 'Content-Type: application/json' -d "{\"ids\":[\"$first_old\"]}" | "$PY" -c 'import json,sys; print(len(json.load(sys.stdin)["result"]))')"
check "old chunk of changed source removed from Qdrant" 0 "$left"
p="$(parity)"; check "exact parity after replacement ($p)" "${p%=*}" "${p#*=}"

echo "== removed source"
rm "$CORPUS/clough/transcripts/docs/bf-004_transcript.doc"
manifest
build; check "removed source reported" 5 $?
build --retire-stale; check "retire removed source" 0 $?
check "lesson count after removal" "$((sources - 1))" "$(q "SELECT count(*) FROM lessons")"
p="$(parity)"; check "exact parity after removal ($p)" "${p%=*}" "${p#*=}"

echo "== deleted canonical output"
rel="$(q "SELECT canonical_html_path FROM lessons WHERE canonical_html_path IS NOT NULL ORDER BY id LIMIT 1")"
rm "$CANONICAL_PATH/$rel"
build; check "missing canonical detected" 1 $?
build --repair-outputs; check "missing canonical repaired" 0 $?
[[ -f "$CANONICAL_PATH/$rel" ]]; check "canonical file restored" 0 $?

echo "build log: kept only on failure"
if [[ "$failures" -ne 0 ]]; then cp "$WORK/build.log" "/tmp/build_resilience_failure.log"; echo "  see /tmp/build_resilience_failure.log"; fi
echo "$failures failure(s)"
exit "$failures"
