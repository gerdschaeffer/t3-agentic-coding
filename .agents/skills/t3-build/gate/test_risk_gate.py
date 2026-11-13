import contextlib
import io
import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import risk_gate as g

POLICY = g.load_policy(Path(__file__).with_name("risk-policy.toml"))
SHA = "a" * 40


def f(path, add=5, rem=0):
    return {"path": path, "additions": add, "deletions": rem}


def rating(sha=SHA, login="owner", **kw):
    fields = {
        "verdict": "APPROVE",
        "regression": 0,
        "security": 0,
        "user": 0,
        "security_pass": "n/a",
        "disputed": "none",
        "reasons": "ok",
        **kw,
    }
    body = f"{g.RATING_MARK}{sha} -->\n" + "\n".join(f"{k}: {v}" for k, v in fields.items())
    return {"login": login, "body": body}


def lane(files, comments=(), policy=POLICY, draft=False):
    r = g.parse_rating(list(comments), SHA, ["owner"])
    return g.decide(*g.rate_files(files, policy), r, policy, draft)


class Floor(unittest.TestCase):
    def test_docs_and_tests_only_with_approval_is_auto(self):
        self.assertEqual(lane([f("README.md"), f("tests/test_x.py")], [rating()])[0], "auto")

    def test_ordinary_code_with_approval_is_auto(self):
        self.assertEqual(lane([f("app/views.py")], [rating(regression=1)])[0], "auto")

    def test_migration_blocks_even_when_reviewer_says_0(self):
        self.assertEqual(lane([f("app/migrations/0002.py")], [rating()])[0], "block")

    def test_gate_config_blocks(self):
        self.assertEqual(lane([f(".github/risk-gate/risk-policy.toml")], [rating()])[0], "block")

    def test_agent_config_blocks(self):
        for path in ["AGENTS.md", "src/CLAUDE.md", ".claude/settings.json", ".mcp.json"]:
            with self.subTest(path=path):
                self.assertEqual(lane([f(path)], [rating()])[0], "block")

    def test_auth_goes_to_review_and_reviewer_cannot_lower_it(self):
        self.assertEqual(
            lane([f("accounts/auth.py")], [rating(security=0, security_pass="clean")])[0], "review"
        )

    def test_dependency_change_goes_to_review(self):
        self.assertEqual(lane([f("pyproject.toml")], [rating()])[0], "review")

    def test_large_diff_goes_to_review(self):
        self.assertEqual(
            lane(
                [f("app/a.py", add=POLICY["limits"]["small_diff_lines"]), f("app/b.py", add=1)],
                [rating()],
            )[0],
            "review",
        )

    def test_removing_test_lines_goes_to_review(self):
        self.assertEqual(lane([f("tests/test_x.py", add=0, rem=20)], [rating()])[0], "review")

    def test_too_many_files_blocks(self):
        files = [f(f"docs/{i}.md", add=0, rem=0) for i in range(g.MAX_FILES)]
        self.assertEqual(lane(files, [rating()])[0], "block")

    def test_rename_is_rated_under_both_names(self):
        moved = {**f("archive/ci.txt"), "previous": ".github/workflows/ci.yml"}
        self.assertEqual(lane([moved], [rating()])[0], "block")
        moved_in = {**f("app/auth/x.py"), "previous": "app/x.py"}
        self.assertEqual(lane([moved_in], [rating(security_pass="clean")])[0], "review")

    def test_symlink_or_submodule_goes_to_review(self):
        link = {**f("unittest"), "link": "symlink or submodule"}
        self.assertEqual(lane([link], [rating()])[0], "review")

    def test_patterns(self):
        self.assertTrue(g.matches("package.json", "*/package.json"))
        self.assertTrue(g.matches("web/package.json", "*/package.json"))
        self.assertFalse(g.matches("mypackage.json", "*/package.json"))
        self.assertTrue(g.matches("migrations/0001.py", "*/migrations/*"))


class Rating(unittest.TestCase):
    def test_reviewer_raises(self):
        self.assertEqual(lane([f("app/views.py")], [rating(regression=2)])[0], "review")

    def test_no_rating_is_review(self):
        result = lane([f("README.md")])
        self.assertEqual(result[0], "review")
        self.assertIn("no trusted risk rating for the head commit", result[3])

    def test_rating_for_other_commit_is_ignored(self):
        self.assertEqual(lane([f("README.md")], [rating(sha="b" * 40)])[0], "review")

    def test_untrusted_author_is_ignored(self):
        self.assertEqual(lane([f("README.md")], [rating(login="stranger")])[0], "review")

    def test_withdraw_by_posting_a_newer_changes_rating(self):
        self.assertEqual(lane([f("README.md")], [rating(), rating(verdict="CHANGES")])[0], "review")

    def test_latest_trusted_rating_wins(self):
        comments = [rating(), rating(verdict="CHANGES")]
        self.assertEqual(lane([f("README.md")], comments)[0], "review")

    def test_malformed_rating_is_no_rating(self):
        self.assertEqual(lane([f("README.md")], [rating(security="high")])[0], "review")
        self.assertEqual(lane([f("README.md")], [rating(security=7)])[0], "review")

    def test_changes_verdict_disputes_and_draft_block_auto(self):
        self.assertEqual(lane([f("README.md")], [rating(verdict="CHANGES")])[0], "review")
        self.assertEqual(lane([f("README.md")], [rating(disputed="R2")])[0], "review")
        self.assertEqual(lane([f("README.md")], [rating()], draft=True)[0], "review")

    def test_marker_must_start_at_character_zero(self):
        shifted = rating()
        shifted["body"] = "\n" + shifted["body"]
        self.assertEqual(lane([f("README.md")], [shifted])[0], "review")

    def test_security_1_needs_clean_pass(self):
        self.assertEqual(lane([f("app/views.py")], [rating(security=1)])[0], "review")
        self.assertEqual(
            lane([f("app/views.py")], [rating(security=1, security_pass="clean")])[0], "auto"
        )


class Live(unittest.TestCase):
    def test_user_impact_counts_only_when_live(self):
        self.assertEqual(lane([f("app/views.py")], [rating(user=3)])[0], "auto")
        self.assertEqual(
            lane([f("app/views.py")], [rating(user=3)], policy={**POLICY, "live": True})[0], "block"
        )

    def test_merge_hours(self):
        policy = {"merge": {"hours": [8, 20], "timezone": "Europe/Berlin"}}
        self.assertTrue(g.in_merge_hours(policy, datetime(2026, 11, 7, 8, 0)))
        self.assertFalse(g.in_merge_hours(policy, datetime(2026, 11, 7, 20, 0)))
        self.assertTrue(g.in_merge_hours({}))


class Card(unittest.TestCase):
    def test_card_is_stable_and_marked(self):
        args = (
            "auto",
            "shadow",
            {"regression": 0, "security": 0, "user": 0},
            [],
            [],
            SHA,
            False,
            True,
        )
        card = g.render_card(*args)
        self.assertTrue(card.startswith(g.CARD_MARK))
        self.assertNotIn(g.RATING_MARK, card)  # a card must never look like a rating
        self.assertEqual(card, g.render_card(*args))


class Main(unittest.TestCase):
    """main() against a fake gh: call order is the security property here."""

    def run_main(
        self,
        files,
        comments,
        mode="enforce",
        armed_after_disable=False,
        env=None,
        policy=None,
        tree=None,
    ):
        calls = []
        comments = list(comments)

        def fake_gh(*args, check=True):
            calls.append(args)
            if args[:2] == ("pr", "view") and "headRefOid,isDraft" in args:
                return json.dumps({"headRefOid": SHA, "isDraft": False})
            if args[:2] == ("pr", "view") and "autoMergeRequest" in args:
                return json.dumps({"autoMergeRequest": {"x": 1} if armed_after_disable else None})
            if args[0] == "api" and args[1].endswith("/files"):
                return "\n".join(
                    json.dumps(
                        {"filename": p, "previous_filename": None, "additions": 5, "deletions": 0}
                    )
                    for p in files
                )
            if args[0] == "api" and "/git/trees/" in args[1]:
                return json.dumps(tree or {"truncated": False, "links": []})
            if args[0] == "api" and args[1].endswith("/comments"):
                return "\n".join(json.dumps(c) for c in comments)
            if args[:2] == ("pr", "comment"):
                comments.append({"login": "owner", "body": args[args.index("--body") + 1]})
            return ""

        # "owner" rates in these tests, whatever rater the repository policy names.
        policy = {**POLICY, "rater": "owner", "mode": mode, **(policy or {})}
        with (
            mock.patch.object(g, "gh", fake_gh),
            mock.patch.object(g, "load_policy", return_value=policy),
            mock.patch.dict(os.environ, {"REPO": "owner/repo", "PR": "7", **(env or {})}),
            contextlib.suppress(SystemExit),
        ):
            g.main()
        statuses = [a for a in calls if a[0] == "api" and "/statuses/" in a[1]]
        merges = [a for a in calls if a[:2] == ("pr", "merge")]
        return calls, statuses, merges

    def test_pending_first_then_auto_merge_after_success(self):
        calls, statuses, merges = self.run_main(["README.md"], [rating()])
        self.assertIn("state=pending", statuses[0])
        self.assertIn("state=success", statuses[-1])
        self.assertIn("--auto", merges[-1])
        self.assertIn(SHA, merges[-1])
        self.assertLess(calls.index(statuses[-1]), calls.index(merges[-1]))

    def test_review_lane_disables_auto_merge_and_never_enables_it(self):
        _, _, merges = self.run_main(["app/auth.py"], [rating(security_pass="clean")])
        self.assertTrue(all("--disable-auto" in m for m in merges))

    def test_failed_disable_leaves_status_pending(self):
        _, statuses, merges = self.run_main(["app/views.py"], [], armed_after_disable=True)
        self.assertEqual([s for s in statuses if "state=pending" not in s], [])
        self.assertFalse(any("--auto" in m for m in merges))

    def test_merge_method_comes_from_policy(self):
        with mock.patch.dict(POLICY, {"merge": {"method": "merge"}}):
            _, _, merges = self.run_main(["README.md"], [rating()])
        self.assertIn("--merge", merges[-1])
        self.assertNotIn("--squash", merges[-1])

    def test_unknown_merge_method_stays_pending(self):
        with mock.patch.dict(POLICY, {"merge": {"method": "fast-forward"}}):
            _, statuses, merges = self.run_main(["README.md"], [rating()])
        self.assertEqual([s for s in statuses if "state=pending" not in s], [])
        self.assertFalse(any("--auto" in m for m in merges))

    def test_dry_run_prints_the_lane_and_writes_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            calls, statuses, merges = self.run_main(
                [".github/workflows/x.yml"], [rating()], env={"RISK_GATE_DRY_RUN": "1"}
            )
        self.assertTrue(out.getvalue().startswith("block R3"))
        self.assertEqual(statuses, [])
        self.assertEqual(merges, [])
        self.assertFalse([a for a in calls if a[:2] == ("pr", "comment")])

    def test_rater_from_policy_counts_after_a_move_to_an_org(self):
        org_repo = {"REPO": "some-org/repo"}
        no_rater = {"rater": None}
        _, statuses, _ = self.run_main(["README.md"], [rating()], env=org_repo, policy=no_rater)
        self.assertIn("review", statuses[-1][-1])  # default: "some-org" is not the rater
        _, statuses, _ = self.run_main(["README.md"], [rating()], env=org_repo)
        self.assertIn("auto", statuses[-1][-1])

    def test_symlinks_from_the_head_tree_go_to_review(self):
        _, statuses, merges = self.run_main(
            ["unittest"], [rating()], tree={"truncated": False, "links": ["unittest"]}
        )
        self.assertIn("review", statuses[-1][-1])
        self.assertFalse(any("--auto" in m for m in merges))
        _, statuses, _ = self.run_main(
            ["README.md"], [rating()], tree={"truncated": True, "links": []}
        )
        self.assertIn("review", statuses[-1][-1])

    def test_shadow_never_merges(self):
        _, _, merges = self.run_main(["README.md"], [rating()], mode="shadow")
        self.assertFalse(any("--auto" in m for m in merges))


if __name__ == "__main__":
    unittest.main()
