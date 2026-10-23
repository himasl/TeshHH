import json
import re
import sys
from datetime import date, timedelta


FIELDS = (
    "Last_name_ru", "First_name_ru", "Middle_name_ru", "Sex_ru", "Birth_date",
    "Birth_place_ru", "Issue_date", "Issue_organisation_code", "Licence_number",
    "Issue_organization_ru",
)
TODAY = date(2026, 10, 1)
LETTERS = dict(zip(
    "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
    "ABVGDEEJZIQKLMNOPRSTUFHC34WXY9678",
))


def read_jsonl(path):
    with open(path, encoding="utf-8") as file:
        for line in file:
            yield json.loads(line)


def clean(text):
    return " ".join(text.upper().split())


def field_text(field):
    if not field:
        return ""
    return clean(" ".join(line["text"] for line in field["lines"]))


def field_conf(field):
    if not field or not field["lines"]:
        return 0.0
    return min((min(line["char_conf"], default=0.0) for line in field["lines"]), default=0.0)


def check_digit(text):
    values = {**{str(i): i for i in range(10)}, **{chr(65 + i): i + 10 for i in range(26)}, "<": 0}
    return str(sum(values.get(char, 100) * (7, 3, 1)[i % 3] for i, char in enumerate(text)) % 10)


def mrz_data(mrz):
    if not mrz or len(mrz["lines"]) != 2:
        return None
    first, second = (line["text"].upper() for line in mrz["lines"])
    if len(first) != 44 or len(second) != 44 or not first.startswith("PNRUS") or second[10:13] != "RUS":
        return None
    if any(not re.fullmatch(r"[A-Z0-9<]", char) for char in first + second):
        return None
    if (check_digit(second[:9]) != second[9] or
            check_digit(second[13:19]) != second[19] or
            check_digit(second[28:41]) != second[42] or
            check_digit(second[:10] + second[13:20] + second[21:43]) != second[43]):
        return None
    return first, second


def latin(text):
    return "".join(LETTERS.get(char, "<" if char in " -" else char) for char in text.upper())


def mrz_matches(fields, mrz):
    if not mrz:
        return False
    first, second = mrz
    names = first[5:].split("<<", 1)
    if len(names) != 2 or latin(fields["Last_name_ru"]) != names[0]:
        return False
    given = names[1].rstrip("<")
    expected = latin(fields["First_name_ru"] + (" " + fields["Middle_name_ru"] if fields["Middle_name_ru"] else ""))
    if given != expected[:len(given)] or len(given) < len(latin(fields["First_name_ru"])):
        return False
    number = re.sub(r"\D", "", fields["Licence_number"])
    if len(number) != 10 or number[:3] + number[4:] != second[:9] or number[3] != second[28]:
        return False
    if fields["Issue_organisation_code"].replace("-", "") != second[35:41]:
        return False
    for name, part in (("Birth_date", second[13:19]), ("Issue_date", second[29:35])):
        try:
            value = date.fromisoformat("-".join(reversed(fields[name].split("."))))
        except ValueError:
            return False
        if value.strftime("%y%m%d") != part:
            return False
    if fields["Sex_ru"] not in ("МУЖ.", "ЖЕН.") or fields["Sex_ru"][0] != {"M": "М", "F": "Ж"}.get(second[20]):
        return False
    return True


def valid_passport(fields):
    try:
        born = date.fromisoformat("-".join(reversed(fields["Birth_date"].split("."))))
        issued = date.fromisoformat("-".join(reversed(fields["Issue_date"].split("."))))
    except ValueError:
        return False
    if not 1906 <= born.year <= 2012:
        return False

    def birthday(age):
        try:
            return born.replace(year=born.year + age)
        except ValueError:
            return date(born.year + age, 2, 28)

    age14, age20, age45 = (birthday(age) for age in (14, 20, 45))
    if not age14 <= issued <= TODAY:
        return False
    if issued < age20 and TODAY > age20 + timedelta(days=90):
        return False
    if issued < age45 and TODAY > age45 + timedelta(days=90):
        return False
    return True


def full_text(field):
    if not field or not field["lines"]:
        return False
    line_height = max(line["box"][3] for line in field["lines"])
    return field["box"][3] < 1.35 * line_height * len(field["lines"])


def process(row):
    fields = {name: field_text(row["viz"].get(name)) for name in FIELDS}
    if any(not fields[name] for name in FIELDS if name != "Middle_name_ru"):
        return {"doc_id": row["doc_id"], "decision": "reject"}
    code = re.sub(r"\D", "", fields["Issue_organisation_code"])
    if len(code) == 6:
        fields["Issue_organisation_code"] = code[:3] + "-" + code[3:]
    number = re.sub(r"\D", "", fields["Licence_number"])
    if len(number) == 10:
        fields["Licence_number"] = number[:2] + " " + number[2:4] + " " + number[4:]
    mrz = mrz_data(row["mrz"])
    if not mrz_matches(fields, mrz) or not valid_passport(fields):
        return {"doc_id": row["doc_id"], "decision": "reject"}
    if not all(full_text(row["viz"][name]) for name in ("Birth_place_ru", "Issue_organization_ru")):
        return {"doc_id": row["doc_id"], "decision": "reject"}
    if min(field_conf(row["viz"][name]) for name in FIELDS if fields[name]) < 0.985:
        return {"doc_id": row["doc_id"], "decision": "reject"}
    mrz_conf = min((conf for line in row["mrz"]["lines"] for conf in line["char_conf"]), default=0.0)
    if row["source"] == "mobile":
        if mrz_conf < 0.99:
            return {"doc_id": row["doc_id"], "decision": "reject"}
    elif row["source"] == "web":
        if mrz_conf < 0.992 or row["quality"]["glare"] > 0.07:
            return {"doc_id": row["doc_id"], "decision": "reject"}
    else:
        return {"doc_id": row["doc_id"], "decision": "reject"}
    return {"doc_id": row["doc_id"], "decision": "accept", "fields": fields}


def main(input_path, output_path):
    with open(output_path, "w", encoding="utf-8") as output:
        for row in read_jsonl(input_path):
            output.write(json.dumps(process(row), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python3 postprocess.py predictions.jsonl answers.jsonl")
    main(sys.argv[1], sys.argv[2])
