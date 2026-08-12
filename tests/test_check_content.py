#!/usr/bin/env python3
"""check_content.py 테스트.

검사기는 임시 디렉터리에 만든 최소 저장소에 대 본다. 실제 저장소를 상대로
하는 것은 마지막 클래스 하나뿐이고, 그것이 CI에서 이 검사를 돌리는 통로다
— `./shoot doctor`를 워크플로에 넣지 않은 것과 같은 이유로 별도 스텝을
두지 않는다. 검사의 값어치는 이미 테스트가 확보한다.

실행:
    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_content  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

CHAPTER = """\
# 1장

## 핵심 개념 설명

내용.

## 주요 명령어/문법

내용.

## 실습 예제

내용.

## 체크리스트

- [ ] 할 수 있다
"""


class FixtureTestCase(unittest.TestCase):
    """티어 하나에 챕터 하나를 갖춘 최소 저장소."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="dba-guide-check-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "01-beginner").mkdir()
        (self.root / "appendix").mkdir()
        (self.root / "exams" / "01-beginner").mkdir(parents=True)
        self.chapter = self.root / "01-beginner" / "01-intro.md"
        self.chapter.write_text(CHAPTER, encoding="utf-8")
        (self.root / "exams" / "01-beginner" / "01-intro.json").write_text(
            json.dumps({"chapter": "01-beginner/01-intro.md"}),
            encoding="utf-8")
        (self.root / "appendix" / "glossary.md").write_text(
            "# 용어집\n", encoding="utf-8")
        self.readme = self.root / "README.md"
        self.readme.write_text(
            "# 목차\n\n"
            "- [1장](01-beginner/01-intro.md)\n"
            "- [용어집](appendix/glossary.md)\n",
            encoding="utf-8")

    def problems(self, check):
        return dict(check_content.check_all(self.root))[check]

    def assertClean(self):
        for name, found in check_content.check_all(self.root):
            self.assertEqual(found, [], f"{name}에서 위반이 나왔다")


class BaselineTest(FixtureTestCase):
    def test_the_fixture_itself_passes(self):
        """픽스처가 처음부터 더러우면 아래 테스트가 전부 무의미해진다."""
        self.assertClean()


class LinkTest(FixtureTestCase):
    def test_broken_relative_link_is_reported(self):
        self.readme.write_text(
            "- [없는 챕터](01-beginner/99-nope.md)\n", encoding="utf-8")
        found = self.problems("links")
        self.assertEqual(len(found), 1, found)
        self.assertIn("99-nope.md", found[0])

    def test_link_from_a_chapter_is_checked_relative_to_that_chapter(self):
        """상대 링크의 기준은 저장소 루트가 아니라 링크를 쓴 파일이다."""
        self.chapter.write_text(
            CHAPTER + "\n[옆 챕터](02-next.md)\n", encoding="utf-8")
        self.assertEqual(len(self.problems("links")), 1)
        (self.root / "01-beginner" / "02-next.md").write_text(
            CHAPTER, encoding="utf-8")
        self.assertEqual(self.problems("links"), [])

    def test_external_urls_are_left_alone(self):
        self.readme.write_text(
            "- [문서](https://example.com/a.md)\n"
            "- [메일](mailto:a@example.com)\n", encoding="utf-8")
        self.assertEqual(self.problems("links"), [])

    def test_pure_anchor_is_left_alone(self):
        self.chapter.write_text(
            CHAPTER + "\n[위로](#핵심-개념-설명)\n", encoding="utf-8")
        self.assertEqual(self.problems("links"), [])

    def test_anchor_on_a_real_file_resolves_to_the_file(self):
        self.chapter.write_text(
            CHAPTER + "\n[용어집](../appendix/glossary.md#락)\n",
            encoding="utf-8")
        self.assertEqual(self.problems("links"), [])

    def test_percent_encoded_path_is_decoded(self):
        (self.root / "01-beginner" / "한 글.md").write_text("x\n", encoding="utf-8")
        self.chapter.write_text(
            CHAPTER + "\n[한 글](%ED%95%9C%20%EA%B8%80.md)\n", encoding="utf-8")
        self.assertEqual(self.problems("links"), [])

    def test_links_inside_code_fences_are_examples_not_links(self):
        """펜스 안의 링크는 설명용 예시다.

        `docs/`가 마크다운 문법을 설명하면서 가짜 경로를 쓰는 일이 있고,
        그것을 깨진 링크로 신고하면 검사기를 끄게 된다.
        """
        self.chapter.write_text(
            CHAPTER + "\n```markdown\n[예시](없는파일.md)\n```\n",
            encoding="utf-8")
        self.assertEqual(self.problems("links"), [])

    def test_links_inside_inline_code_are_examples_not_links(self):
        """인라인 코드 안의 링크도 같은 이유로 예시다.

        펜스와 근거가 같은데 인라인 코드만 빠져 있었다. 마크다운 문법을
        표로 설명하는 문서는 `[글자](url)` 를 셀 안에 백틱으로 넣지 펜스로
        감싸지 않는다 — 표 셀 안에서는 펜스를 쓸 수 없기 때문이다.
        """
        self.chapter.write_text(
            CHAPTER + "\n| 문법 | 뜻 |\n|---|---|\n"
            "| `[글자](url)` | 링크 |\n",
            encoding="utf-8")
        self.assertEqual(self.problems("links"), [])

    def test_image_paths_are_checked_too(self):
        self.chapter.write_text(
            CHAPTER + "\n![그림](없는그림.png)\n", encoding="utf-8")
        self.assertEqual(len(self.problems("links")), 1)

    def test_link_to_a_directory_is_fine(self):
        (self.root / "01-beginner" / "labs").mkdir()
        self.chapter.write_text(
            CHAPTER + "\n[실습](labs/)\n", encoding="utf-8")
        self.assertEqual(self.problems("links"), [])


class OrphanTest(FixtureTestCase):
    def test_chapter_missing_from_readme_is_reported(self):
        (self.root / "01-beginner" / "02-next.md").write_text(
            CHAPTER, encoding="utf-8")
        (self.root / "exams" / "01-beginner" / "02-next.json").write_text(
            "{}", encoding="utf-8")
        found = self.problems("orphans")
        self.assertEqual(len(found), 1, found)
        self.assertIn("02-next.md", found[0])

    def test_appendix_is_covered_too(self):
        (self.root / "appendix" / "matrix.md").write_text("# 표\n", encoding="utf-8")
        self.assertEqual(len(self.problems("orphans")), 1)

    def test_nested_document_is_covered(self):
        """`03-advanced/labs/**` 처럼 하위 디렉터리에 놓인 문서도 목차에 있어야 한다."""
        (self.root / "01-beginner" / "labs").mkdir()
        (self.root / "01-beginner" / "labs" / "README.md").write_text(
            "# 실습\n", encoding="utf-8")
        self.assertEqual(len(self.problems("orphans")), 1)


class StructureTest(FixtureTestCase):
    def test_missing_section_is_reported(self):
        self.chapter.write_text(
            CHAPTER.replace("## 실습 예제\n\n내용.\n", ""), encoding="utf-8")
        found = self.problems("structure")
        self.assertEqual(len(found), 1, found)
        self.assertIn("실습 예제", found[0])

    def test_sections_out_of_order_are_reported(self):
        body = ("# 1장\n\n## 체크리스트\n\n- [ ] x\n\n## 핵심 개념 설명\n\n"
                "내용.\n\n## 주요 명령어/문법\n\n내용.\n\n## 실습 예제\n\n내용.\n")
        self.chapter.write_text(body, encoding="utf-8")
        self.assertTrue(self.problems("structure"))

    def test_numbered_headings_are_accepted(self):
        """`## 1. 핵심 개념 설명` 형태를 쓰는 챕터가 실제로 있다."""
        numbered = CHAPTER
        for i, name in enumerate(
                ["핵심 개념 설명", "주요 명령어/문법", "실습 예제", "체크리스트"], 1):
            numbered = numbered.replace(f"## {name}", f"## {i}. {name}")
        self.chapter.write_text(numbered, encoding="utf-8")
        self.assertEqual(self.problems("structure"), [])

    def test_subtitle_after_the_section_name_is_accepted(self):
        """`## 실습 예제 — PostgreSQL PITR` 형태도 실제로 있다."""
        self.chapter.write_text(
            CHAPTER.replace("## 실습 예제", "## 실습 예제 — PostgreSQL PITR"),
            encoding="utf-8")
        self.assertEqual(self.problems("structure"), [])

    def test_concept_chapters_may_say_주요_개념(self):
        self.chapter.write_text(
            CHAPTER.replace("주요 명령어/문법", "주요 개념/문법"), encoding="utf-8")
        self.assertEqual(self.problems("structure"), [])

    def test_extra_sections_between_the_four_are_allowed(self):
        self.chapter.write_text(
            CHAPTER.replace("## 체크리스트",
                            "## 백업 주기 설계\n\n내용.\n\n## 체크리스트"),
            encoding="utf-8")
        self.assertEqual(self.problems("structure"), [])

    def test_overview_is_exempt(self):
        (self.root / "01-beginner" / "00-overview.md").write_text(
            "# 개요\n\n## 선수 지식\n", encoding="utf-8")
        self.readme.write_text(
            self.readme.read_text(encoding="utf-8")
            + "- [개요](01-beginner/00-overview.md)\n", encoding="utf-8")
        self.assertClean()

    def test_cheatsheet_is_exempt(self):
        (self.root / "01-beginner" / "07-commands-cheatsheet.md").write_text(
            "# 치트시트\n\n| a | b |\n|---|---|\n", encoding="utf-8")
        self.readme.write_text(
            self.readme.read_text(encoding="utf-8")
            + "- [치트시트](01-beginner/07-commands-cheatsheet.md)\n",
            encoding="utf-8")
        self.assertClean()


class BankTest(FixtureTestCase):
    def test_chapter_without_a_bank_is_reported(self):
        (self.root / "exams" / "01-beginner" / "01-intro.json").unlink()
        found = self.problems("banks")
        self.assertEqual(len(found), 1, found)
        self.assertIn("01-intro", found[0])

    def test_bank_in_the_wrong_tier_does_not_count(self):
        (self.root / "exams" / "02-intermediate").mkdir(parents=True)
        (self.root / "exams" / "01-beginner" / "01-intro.json").rename(
            self.root / "exams" / "02-intermediate" / "01-intro.json")
        self.assertEqual(len(self.problems("banks")), 1)

    def test_bank_chapter_field_matching_the_chapter_path_is_clean(self):
        """픽스처의 은행은 이미 맞는 `chapter`를 적어 둔다 — 잡음이 없어야 한다."""
        self.assertEqual(self.problems("banks"), [])

    def test_bank_chapter_field_mismatch_is_reported(self):
        """챕터를 옮기면서 은행의 `chapter`를 안 고치면, 읽기 목록에는 그
        기록이 안 보이는데 `exam` 목록에는 보이는 식으로 같은 정보가 화면
        마다 다르게 보인다 — `reading.chapter_labels`가 이 필드를 전제로
        `exam.best_result_for`에 챕터 경로를 그대로 넘기기 때문이다.
        """
        (self.root / "exams" / "01-beginner" / "01-intro.json").write_text(
            json.dumps({"chapter": "01-beginner/other.md"}), encoding="utf-8")
        found = self.problems("banks")
        self.assertEqual(len(found), 1, found)
        self.assertIn("01-intro.json", found[0])
        self.assertIn("01-beginner/01-intro.md", found[0])

    def test_bank_missing_chapter_field_is_reported(self):
        (self.root / "exams" / "01-beginner" / "01-intro.json").write_text(
            "{}", encoding="utf-8")
        found = self.problems("banks")
        self.assertEqual(len(found), 1, found)

    def test_unreadable_bank_json_is_reported_not_crashed(self):
        (self.root / "exams" / "01-beginner" / "01-intro.json").write_text(
            "{내용이 아니다", encoding="utf-8")
        found = self.problems("banks")
        self.assertEqual(len(found), 1, found)
        self.assertIn("01-intro.json", found[0])


class ExitCodeTest(FixtureTestCase):
    """`main()`이 종료 코드로 결과를 알려야 릴리스 절차에서 쓸 수 있다."""

    def run_main(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_content.main(["--root", str(self.root)])
        return rc, buf.getvalue()

    def test_clean_repo_exits_zero(self):
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertIn("모두 통과", out)

    def test_violation_exits_nonzero_and_names_the_file(self):
        self.readme.write_text("- [없다](01-beginner/99-nope.md)\n",
                               encoding="utf-8")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("99-nope.md", out)
        self.assertIn("[links]", out)


class ShippedContentTest(unittest.TestCase):
    """저장소의 실제 본문이 검사를 통과하는지.

    이 클래스가 CI에서 이 검사를 돌리는 통로다 — 워크플로에 스텝을 따로
    두지 않는다.
    """

    def _skip_on_a_vendor_branch(self):
        """`main` 위에서만 성립하는 검사는 벤더 브랜치에서 건너뛴다.

        벤더 브랜치는 이미 필터된 파생 뷰다. 어느 챕터의 한 절이 통째로
        `<!-- dbms:x -->` 안에 들어 있으면 다른 두 벤더의 뷰에서는 그 절이
        사라지고, 네 절 규약이 깨진 것처럼 보인다 — 검사기가 고장 나서가
        아니라 그 브랜치에는 실제로 없기 때문이다. 마커 유무로 트리를 가른다.
        """
        marked = any("<!-- dbms:" in p.read_text(encoding="utf-8")
                     for p in check_content.chapters(REPO_ROOT))
        if not marked:
            self.skipTest("벤더 브랜치 — 이미 필터된 뷰다")

    def test_links_resolve(self):
        self.assertEqual(check_content.check_links(REPO_ROOT), [])

    def test_no_orphan_documents(self):
        self.assertEqual(check_content.check_orphans(REPO_ROOT), [])

    def test_chapters_follow_the_four_section_shape(self):
        self._skip_on_a_vendor_branch()
        self.assertEqual(check_content.check_structure(REPO_ROOT), [])

    def test_every_chapter_has_a_question_bank(self):
        self.assertEqual(check_content.check_banks(REPO_ROOT), [])

    def test_there_is_something_to_check(self):
        """검사 대상이 0건이면 위의 통과는 아무 의미가 없다."""
        self.assertGreater(len(check_content.chapters(REPO_ROOT)), 20)
        self.assertGreater(
            len(check_content.markdown_files(REPO_ROOT)), 30)


if __name__ == "__main__":
    unittest.main()
