"""GitHub Actions에서 실행되는 진입점.

Apps Script가 새 스캔을 발견하면 repository_dispatch로 이 작업을 깨운다.
필요한 값은 환경변수로 받는다 (워크플로 파일 참고).
"""
import os
import re
import sys
import traceback

from translator.cloud import Drive, Gas
from translator.config import DEFAULT_GLOSSARY
from translator.glossary import glossary_to_text, parse_user_glossary
from translator.pipeline import run_pipeline


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "").strip()
    if required and not v:
        sys.exit(f"환경변수 {name} 가 없습니다.")
    return v


def safe_stem(name: str) -> str:
    stem = re.sub(r"\.pdf$", "", name, flags=re.I)
    return re.sub(r'[\\/:*?"<>|]', "_", stem).strip() or "scan"


def main():
    gas = Gas(env("GAS_URL"), env("GAS_SECRET"))
    file_id = env("JOB_FILE_ID")
    file_name = env("JOB_FILE_NAME")
    out_folder = env("JOB_OUT_FOLDER_ID")
    work_folder = env("JOB_WORK_FOLDER_ID")
    glossary_id = env("JOB_GLOSSARY_FILE_ID", required=False)
    run_url = env("RUN_URL", required=False)

    stem = safe_stem(file_name)
    print(f"작업: {file_name}")
    gas.call("status", file_id=file_id, status="running", run_url=run_url)

    drive = Drive(gas)
    work_name = f"{stem}.work.json"
    work = {}
    try:
        pdf = drive.download(file_id)
        print(f"다운로드: {len(pdf) / 1e6:.1f} MB")

        terms = dict(DEFAULT_GLOSSARY)
        if glossary_id:
            terms = parse_user_glossary(drive.download(glossary_id).decode("utf-8", "ignore")) or terms

        work = drive.load_json(work_folder, work_name)
        if work:
            print("이전 중간 결과를 이어서 사용합니다.")

        out_pdf, report = run_pipeline(
            pdf, work, terms,
            save=lambda w: drive.save_json(work_folder, work_name, w),
        )
        out_id = drive.upload(out_folder, f"{stem}_KO.pdf", out_pdf, "application/pdf")
        drive.upload(work_folder, f"{stem}_용어집.txt",
                     glossary_to_text(work["glossary"]).encode("utf-8"), "text/plain")
        gas.call("status", file_id=file_id, status="done", out_file_id=out_id,
                 report=report, run_url=run_url)
        print("업로드 완료")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        try:
            if work:
                drive.save_json(work_folder, work_name, work)
        except Exception:  # noqa: BLE001
            pass
        gas.call("status", file_id=file_id, status="failed",
                 message=f"{type(e).__name__}: {e}"[:800], run_url=run_url)
        sys.exit(1)


if __name__ == "__main__":
    main()
