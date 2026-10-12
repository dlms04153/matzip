"""
엑셀(CSV)에 정리한 가게를 네이버 API로 조회해서 shops.json에 한꺼번에 추가·갱신합니다.

1) new_shops_template.csv를 엑셀로 열어 가게를 적고, 'CSV UTF-8(쉼표로 분리)'로 저장 (이름: new_shops.csv)
2) python import_shops.py            (다른 파일이면: python import_shops.py 파일이름.csv)
3) 결과 요약을 확인하고, shops.json을 GitHub에 올리면 끝

- 가게 이름만 적어도 주소·좌표·업종은 네이버 API가 채워 줍니다. (칸을 비우면 그 항목은 건드리지 않아요)
- shops.json에 같은 이름의 가게가 이미 있으면, 엑셀에 적은 칸만 덮어써서 '갱신'합니다.
"""
import csv, json, re, math, sys, html, shutil, time
import requests

# ===== 여기 두 줄만 바꾸세요 (collect.py와 같은 값) =====
CLIENT_ID = "여기에_Client_ID"
CLIENT_SECRET = "여기에_Client_Secret"
# =========================================================

URL = "https://naverapihub.apigw.ntruss.com/search/v1/local"
FILE = "shops.json"
ERICA = (37.2966, 126.8354)
RADIUS_KM = 1.3

VALUES = {"drink", "date", "solo", "team", "parents", "cafe_mood", "cafe_study"}
SITU = [("분좋카", "cafe_mood"), ("카공", "cafe_study"), ("술", "drink"), ("데이트", "date"),
        ("혼밥", "solo"), ("회식", "team"), ("부모님", "parents")]
EN = {"일": "sun", "월": "mon", "화": "tue", "수": "wed", "목": "thu", "금": "fri", "토": "sat"}
TIME = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$")


def km(lat1, lng1, lat2, lng2):
    r, p = 6371, math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def coord(v):
    x = float(v)
    return x / 1e7 if abs(x) > 1000 else x


def ok_time(v):
    return v in ("24h", "24시간") or bool(TIME.match(v))


def get(row, key):
    return (row.get(key) or "").strip()


def build_fields(row, warn):
    """엑셀에서 적은 칸만 골라 shops.json 필드로 바꿈"""
    f = {}
    sit = []
    for tok in re.split(r"[;,/·\n]+", get(row, "상황")):
        key = tok.strip().replace(" ", "")
        if not key:
            continue
        if key in VALUES:
            val = key
        else:
            val = next((v for w, v in SITU if w in key), None)
        if val is None:
            warn.append(f"상황 '{tok.strip()}'은(는) 알 수 없는 분류라 건너뜀")
        elif val not in sit:
            sit.append(val)
    if get(row, "상황"):
        f["situations"] = sit
    for col, k, lo, hi in (("가격대", "price", 1, 3), ("최대인원", "maxGroup", 1, 999)):
        v = get(row, col)
        if v:
            if v.isdigit() and lo <= int(v) <= hi:
                f[k] = int(v)
            else:
                warn.append(f"{col} '{v}'은(는) {lo}~{hi} 숫자여야 해서 건너뜀")
    if get(row, "태그"):
        f["tags"] = [t.strip() for t in re.split(r"[;,]", get(row, "태그")) if t.strip()]
    if get(row, "한줄소개"):
        f["summary"] = get(row, "한줄소개")
    if get(row, "사진"):
        f["photo"] = get(row, "사진")
    if get(row, "추천메뉴"):
        f["menu"] = get(row, "추천메뉴")
    h = get(row, "영업시간")
    if h:
        if ok_time(h):
            f["hours"] = h
        else:
            warn.append(f"영업시간 '{h}' 형식이 달라 건너뜀")
    by_day = {}
    for part in filter(None, get(row, "요일별영업시간").replace(" ", "").split(";")):
        d, _, v = part.partition("=")
        if d in EN.values() and ok_time(v):
            by_day[d] = v
        else:
            warn.append(f"요일별영업시간 '{part}' 형식이 달라 건너뜀")
    if by_day:
        f["hoursByDay"] = by_day
    closed = [EN.get(d, d) for d in re.split(r"[;,\s·]+", get(row, "휴무요일")) if d]
    closed = [d for d in closed if d in EN.values()]
    if closed:
        f["closedDays"] = closed
    b = get(row, "브레이크타임")
    if b:
        if TIME.match(b):
            f["breakTime"] = b
        else:
            warn.append(f"브레이크타임 '{b}' 형식이 달라 건너뜀")
    return f


def search_place(name, region):
    """네이버 지역 검색 -> 이름이 맞고 지역 안에 있는 첫 결과"""
    key = name.replace(" ", "")
    for q in (f"{name} {region}", name):
        r = requests.get(
            URL,
            headers={"X-NCP-APIGW-API-KEY-ID": CLIENT_ID, "X-NCP-APIGW-API-KEY": CLIENT_SECRET},
            params={"query": q, "display": 5},
        )
        if r.status_code != 200:
            print(f"오류({r.status_code}): {r.text}")
            print("키가 맞는지, NAVER API HUB Application에 '지역' API를 선택했는지 확인하세요.")
            raise SystemExit
        time.sleep(0.2)
        for it in r.json().get("items", []):
            title = html.unescape(re.sub(r"<.*?>", "", it["title"])).strip()
            t = title.replace(" ", "")
            lat, lng = coord(it["mapy"]), coord(it["mapx"])
            in_area = "고잔동" in it["address"] or km(lat, lng, *ERICA) <= RADIUS_KM
            if in_area and (key in t or t in key):
                return {"name": title, "category": it["category"], "address": it["roadAddress"] or it["address"],
                        "lat": lat, "lng": lng,
                        "area": "ERICA 앞" if "상록구" in it["address"] else "고잔동 일대"}
    return None


if CLIENT_ID.startswith("여기에"):
    print("먼저 파일 위쪽의 CLIENT_ID와 CLIENT_SECRET을 채워 주세요.")
    raise SystemExit

csv_name = sys.argv[1] if len(sys.argv) > 1 else "new_shops.csv"
try:
    rows = list(csv.DictReader(open(csv_name, encoding="utf-8-sig")))
except FileNotFoundError:
    print(f"{csv_name} 파일이 없어요. 이 스크립트와 같은 폴더에 두었는지 확인하세요.")
    raise SystemExit

shops = json.load(open(FILE, encoding="utf-8"))
shutil.copy(FILE, "shops.backup.json")
added = updated = 0
failed = []

for i, row in enumerate(rows, start=2):          # 엑셀의 행 번호(1행은 제목)
    name = get(row, "가게이름")
    if not name or name.startswith("#"):
        continue
    warn = []
    fields = build_fields(row, warn)
    same = [s for s in shops if s["name"] == name]
    target, base = None, None

    if len(same) == 1:
        target = same[0]
    else:
        if get(row, "위도") and get(row, "경도"):      # API를 못 쓰는 가게는 좌표를 직접 입력
            lat, lng = float(get(row, "위도")), float(get(row, "경도"))
            base = {"name": name, "category": get(row, "업종") or "음식점", "address": get(row, "주소"),
                    "lat": lat, "lng": lng,
                    "area": "ERICA 앞" if km(lat, lng, *ERICA) <= RADIUS_KM else "고잔동 일대"}
        else:
            base = search_place(name, get(row, "지역") or "안산")
        if base is None:
            failed.append((i, name, "검색 결과가 없거나 지역(고잔동·ERICA) 밖이에요. 지점명까지 정확히 쓰거나 위도·경도 칸을 채워 보세요"))
            continue
        target = next((s for s in shops if s["name"] == base["name"] and s["address"] == base["address"]), None)

    if target is not None:
        target.update(fields)
        if "situations" in fields:
            target.pop("draft", None)                   # 직접 분류했으니 '초안' 표시 제거
        updated += 1
        print(f"↻ 갱신 [{i}행] {target['name']}")
    else:
        shop = {**base, "situations": [], "price": 2, "maxGroup": 8, "tags": [], "summary": "", "menu": ""}
        shop.update(fields)
        shops.append(shop)
        added += 1
        print(f"+ 추가 [{i}행] {shop['name']} ({shop['address']})")
        if not shop["situations"]:
            warn.append("상황을 적지 않아서 '전체 보기'에서만 보여요")
    for w in warn:
        print(f"    ! {w}")

with open(FILE, "w", encoding="utf-8") as f:
    json.dump(shops, f, ensure_ascii=False, indent=2)

print(f"\n완료! 추가 {added}곳 · 갱신 {updated}곳 · 실패 {len(failed)}곳 (총 {len(shops)}곳)")
for i, name, why in failed:
    print(f"  ✖ [{i}행] {name}: {why}")
print("이전 파일은 shops.backup.json에 백업했어요.")
