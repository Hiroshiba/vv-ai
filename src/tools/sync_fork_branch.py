"""フォーク側 feature ブランチと upstream 側 PR ブランチを双方向同期するツール。"""

import argparse
import subprocess
import sys


def main() -> None:
    """フォーク側 feature ブランチと upstream 側 PR ブランチを双方向同期する。"""
    parser = argparse.ArgumentParser(
        description="フォーク側 feature ブランチと upstream 側 PR ブランチを双方向同期する"
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    for name in ("push", "pull"):
        sub = subparsers.add_parser(
            name,
            help="fork→upstream に同期する" if name == "push" else "upstream→fork に同期する",
        )
        sub.add_argument("--branch", help="対象ブランチ名（省略時はカレントブランチ）")
        sub.add_argument("--force", action="store_true", help="形が合わないとき force-with-lease push で全置換する")
        sub.add_argument("--dry-run", action="store_true", help="実際には push せず計画だけ表示する")
        sub.add_argument("--yes", action="store_true", help="確認プロンプトをスキップする")

    args = parser.parse_args()

    syncer = ForkBranchSyncer(
        branch=args.branch,
        force=args.force,
        dry_run=args.dry_run,
        yes=args.yes,
    )

    if args.subcommand == "push":
        syncer.push()
    else:
        syncer.pull()


class ForkBranchSyncer:
    """フォーク側ブランチと upstream 側ブランチの同期を担う。"""

    def __init__(self, branch: str | None, force: bool, dry_run: bool, yes: bool) -> None:
        self.branch = branch
        self.force = force
        self.dry_run = dry_run
        self.yes = yes

    def push(self) -> None:
        """fork 側 feature の新規コミットを upstream 側 feature に反映する。"""
        self._fetch()
        feature = self._resolve_feature_branch()
        fork_main = self._detect_main("origin")
        upstream_main = self._detect_main("upstream")

        print(f"feature: {feature}")
        print(f"fork_main: {fork_main}, upstream_main: {upstream_main}")

        self._maybe_catch_up(feature, fork_main, upstream_main)

        fork_commits = self._patch_ids(f"{fork_main}..{feature}")
        upstream_branch = f"upstream/{feature}"

        if self._branch_exists(upstream_branch):
            upstream_commits = self._patch_ids(f"{upstream_main}..{upstream_branch}")
        else:
            upstream_commits = {}
            print(f"{upstream_branch} が存在しないため {upstream_main} から作成します")
            if not self.dry_run:
                self._run(["git", "branch", f"upstream_sync_tmp_{feature}", upstream_main], capture=False, input=None, check=True)
                self._run(["git", "push", "upstream", f"upstream_sync_tmp_{feature}:{feature}"], capture=False, input=None, check=True)
                self._run(["git", "branch", "-D", f"upstream_sync_tmp_{feature}"], capture=False, input=None, check=True)
                self._fetch()
                upstream_commits = self._patch_ids(f"{upstream_main}..{upstream_branch}")

        new_commits = _commits_not_in(fork_commits, upstream_commits)
        pull_only = _commits_not_in(upstream_commits, fork_commits)

        if pull_only:
            print(
                f"エラー: upstream 側に {len(pull_only)} 件の未取り込みコミットがあります。先に pull を実行してください。",
                file=sys.stderr,
            )
            sys.exit(1)

        if not new_commits:
            print("同期済みです。push するコミットはありません。")
            return

        print(f"cherry-pick するコミット: {len(new_commits)} 件")
        for pid, hsh in new_commits:
            print(f"  {hsh[:12]} (patch-id: {pid[:12]})")

        if self.force:
            self._force_replace(feature, new_commits, "upstream")
        else:
            self._cherry_pick_and_push(feature, new_commits, upstream_branch, "upstream")

    def pull(self) -> None:
        """upstream 側 feature の新規コミットを fork 側 feature に取り込む。"""
        self._fetch()
        feature = self._resolve_feature_branch()
        fork_main = self._detect_main("origin")
        upstream_main = self._detect_main("upstream")

        print(f"feature: {feature}")
        print(f"fork_main: {fork_main}, upstream_main: {upstream_main}")

        self._maybe_catch_up(feature, fork_main, upstream_main)

        upstream_branch = f"upstream/{feature}"
        if not self._branch_exists(upstream_branch):
            print(f"エラー: {upstream_branch} が存在しません。", file=sys.stderr)
            sys.exit(1)

        fork_commits = self._patch_ids(f"{fork_main}..{feature}")
        upstream_commits = self._patch_ids(f"{upstream_main}..{upstream_branch}")

        new_commits = _commits_not_in(upstream_commits, fork_commits)
        push_only = _commits_not_in(fork_commits, upstream_commits)

        if push_only:
            print(
                f"エラー: fork 側に {len(push_only)} 件の未同期コミットがあります。先に push を実行してください。",
                file=sys.stderr,
            )
            sys.exit(1)

        if not new_commits:
            print("同期済みです。pull するコミットはありません。")
            return

        print(f"cherry-pick するコミット: {len(new_commits)} 件")
        for pid, hsh in new_commits:
            print(f"  {hsh[:12]} (patch-id: {pid[:12]})")

        if self.force:
            self._force_replace(feature, new_commits, "origin")
        else:
            self._cherry_pick_and_push(feature, new_commits, feature, "origin")

    def _fetch(self) -> None:
        """origin と upstream を fetch する。"""
        self._run(["git", "fetch", "origin"], capture=False, input=None, check=True)
        self._run(["git", "fetch", "upstream"], capture=False, input=None, check=True)

    def _resolve_feature_branch(self) -> str:
        """対象ブランチ名を解決する。"""
        if self.branch:
            return self.branch
        result = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True, input=None, check=True)
        branch = result.stdout.strip()
        if branch == "HEAD":
            print("エラー: detached HEAD 状態です。--branch で対象ブランチを指定してください。", file=sys.stderr)
            sys.exit(1)
        return branch

    def _detect_main(self, remote: str) -> str:
        """remote/HEAD からメインブランチ名を検出する。"""
        result = self._run(
            ["git", "rev-parse", "--abbrev-ref", f"{remote}/HEAD"],
            capture=True,
            input=None,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        for candidate in (f"{remote}/main", f"{remote}/master"):
            r = self._run(["git", "rev-parse", "--verify", candidate], capture=True, input=None, check=False)
            if r.returncode == 0:
                return candidate
        print(f"エラー: {remote} のメインブランチを検出できません。", file=sys.stderr)
        sys.exit(1)

    def _branch_exists(self, ref: str) -> bool:
        """ref が存在するかどうか確認する。"""
        result = self._run(["git", "rev-parse", "--verify", ref], capture=True, input=None, check=False)
        return result.returncode == 0

    def _patch_ids(self, revision_range: str) -> dict[str, str]:
        """revision_range のコミット列について {patch_id: commit_hash} を返す。"""
        log = self._run(
            ["git", "log", "--no-merges", "--format=%H", revision_range],
            capture=True,
            input=None,
            check=True,
        )
        hashes = [h for h in log.stdout.strip().splitlines() if h]
        result: dict[str, str] = {}
        for hsh in hashes:
            show = self._run(["git", "show", hsh], capture=True, input=None, check=True)
            pid_result = self._run(["git", "patch-id", "--stable"], capture=True, input=show.stdout, check=True)
            line = pid_result.stdout.strip()
            if line:
                pid = line.split()[0]
                result[pid] = hsh
        return result

    def _maybe_catch_up(self, feature: str, fork_main: str, upstream_main: str) -> None:
        """upstream/main が進んでいたら追従処理を実行する。"""
        fork_main_hash = self._run(["git", "rev-parse", fork_main], capture=True, input=None, check=True).stdout.strip()
        upstream_main_hash = self._run(["git", "rev-parse", upstream_main], capture=True, input=None, check=True).stdout.strip()

        base = self._run(
            ["git", "merge-base", fork_main_hash, upstream_main_hash],
            capture=True,
            input=None,
            check=True,
        ).stdout.strip()

        if base == upstream_main_hash:
            return

        print(f"upstream/main が {fork_main} より進んでいます。追従処理を実行します。")
        if not self.yes:
            answer = input("続行しますか？ [y/N]: ").strip().lower()
            if answer != "y":
                print("キャンセルしました。")
                sys.exit(0)

        fork_main_name = fork_main.removeprefix("origin/")

        if self.dry_run:
            print(f"[dry-run] {fork_main_name} に {upstream_main} を merge commit")
            print(f"[dry-run] {feature} に {fork_main_name} を merge commit")
            print(f"[dry-run] upstream/{feature} に {upstream_main} を merge commit")
            return

        current = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True, input=None, check=True).stdout.strip()
        self._run(["git", "checkout", fork_main_name], capture=False, input=None, check=True)
        result = self._run(["git", "merge", "--no-ff", upstream_main], capture=False, input=None, check=False)
        if result.returncode != 0:
            print("エラー: merge が conflict しました。手作業で解決後に再実行してください。", file=sys.stderr)
            sys.exit(1)
        self._run(["git", "push", "origin", fork_main_name], capture=False, input=None, check=True)

        self._run(["git", "checkout", feature], capture=False, input=None, check=True)
        result = self._run(["git", "merge", "--no-ff", fork_main_name], capture=False, input=None, check=False)
        if result.returncode != 0:
            print("エラー: merge が conflict しました。手作業で解決後に再実行してください。", file=sys.stderr)
            sys.exit(1)

        upstream_branch = f"upstream/{feature}"
        if self._branch_exists(upstream_branch):
            tmp = f"upstream_catchup_tmp_{feature}"
            upstream_branch_hash = self._run(["git", "rev-parse", upstream_branch], capture=True, input=None, check=True).stdout.strip()
            self._run(["git", "branch", tmp, upstream_branch_hash], capture=False, input=None, check=True)
            self._run(["git", "checkout", tmp], capture=False, input=None, check=True)
            result = self._run(["git", "merge", "--no-ff", upstream_main], capture=False, input=None, check=False)
            if result.returncode != 0:
                print(
                    f"エラー: merge が conflict しました。手作業で解決後、`git branch -D {tmp}` で一時ブランチを削除してから再実行してください。",
                    file=sys.stderr,
                )
                sys.exit(1)
            self._run(["git", "push", "upstream", f"{tmp}:{feature}"], capture=False, input=None, check=True)
            self._run(["git", "checkout", feature], capture=False, input=None, check=True)
            self._run(["git", "branch", "-D", tmp], capture=False, input=None, check=True)
            self._fetch()

        if current != feature:
            self._run(["git", "checkout", current], capture=False, input=None, check=True)

    def _cherry_pick_and_push(
        self,
        feature: str,
        new_commits: list[tuple[str, str]],
        local_branch: str,
        remote: str,
    ) -> None:
        """new_commits を local_branch に cherry-pick して remote に push する。"""
        if self.dry_run:
            print(f"[dry-run] {len(new_commits)} 件を {local_branch} に cherry-pick して {remote} へ push")
            return

        current = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True, input=None, check=True).stdout.strip()

        if remote == "upstream":
            tmp = f"upstream_sync_tmp_{feature}"
            base_hash = self._run(["git", "rev-parse", local_branch], capture=True, input=None, check=True).stdout.strip()
            self._run(["git", "branch", tmp, base_hash], capture=False, input=None, check=True)
            self._run(["git", "checkout", tmp], capture=False, input=None, check=True)
            work_branch = tmp
        else:
            if current != local_branch:
                self._run(["git", "checkout", local_branch], capture=False, input=None, check=True)
            work_branch = local_branch

        for pid, hsh in reversed(new_commits):
            print(f"cherry-pick: {hsh[:12]} (patch-id: {pid[:12]})")
            result = self._run(["git", "cherry-pick", hsh], capture=False, input=None, check=False)
            if result.returncode != 0:
                if remote == "upstream":
                    print(
                        f"エラー: cherry-pick が conflict しました。手作業で解決後に `git cherry-pick --continue` を実行してください。完了後に `git branch -D {work_branch}` で一時ブランチを削除してください。",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "エラー: cherry-pick が conflict しました。手作業で解決後に `git cherry-pick --continue` を実行してください。",
                        file=sys.stderr,
                    )
                sys.exit(1)

        if remote == "upstream":
            self._run(["git", "push", "upstream", f"{work_branch}:{feature}"], capture=False, input=None, check=True)
            self._run(["git", "checkout", current], capture=False, input=None, check=True)
            self._run(["git", "branch", "-D", work_branch], capture=False, input=None, check=True)
        else:
            self._run(["git", "push", remote, local_branch], capture=False, input=None, check=True)
            if current != local_branch:
                self._run(["git", "checkout", current], capture=False, input=None, check=True)

    def _force_replace(
        self,
        feature: str,
        new_commits: list[tuple[str, str]],
        remote: str,
    ) -> None:
        """--force 時に相手側を全置換して force-with-lease push する。"""
        if remote == "upstream":
            target_branch = f"upstream_sync_tmp_{feature}"
        else:
            target_branch = feature

        if self.dry_run:
            print(f"[dry-run] --force: {remote}/{feature} を全置換して force-with-lease push")
            return

        current = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True, input=None, check=True).stdout.strip()

        if remote == "upstream":
            upstream_branch = f"upstream/{feature}"
            if self._branch_exists(upstream_branch):
                base_hash = self._run(["git", "rev-parse", upstream_branch], capture=True, input=None, check=True).stdout.strip()
            else:
                upstream_main_name = self._detect_main("upstream").removeprefix("upstream/")
                base_hash = self._run(["git", "rev-parse", f"upstream/{upstream_main_name}"], capture=True, input=None, check=True).stdout.strip()
            self._run(["git", "branch", target_branch, base_hash], capture=False, input=None, check=True)
            self._run(["git", "checkout", target_branch], capture=False, input=None, check=True)
        else:
            self._run(["git", "checkout", feature], capture=False, input=None, check=True)

        for pid, hsh in reversed(new_commits):
            print(f"cherry-pick: {hsh[:12]} (patch-id: {pid[:12]})")
            result = self._run(["git", "cherry-pick", hsh], capture=False, input=None, check=False)
            if result.returncode != 0:
                cleanup_hint = f"解決後、`git branch -D {target_branch}` で一時ブランチを削除してから再実行してください。" if remote == "upstream" else "解決後に再実行してください。"
                print(
                    f"エラー: cherry-pick が conflict しました。{cleanup_hint}",
                    file=sys.stderr,
                )
                sys.exit(1)

        if remote == "upstream":
            self._run(["git", "push", "--force-with-lease", "upstream", f"{target_branch}:{feature}"], capture=False, input=None, check=True)
            self._run(["git", "checkout", current], capture=False, input=None, check=True)
            self._run(["git", "branch", "-D", target_branch], capture=False, input=None, check=True)
        else:
            self._run(["git", "push", "--force-with-lease", "origin", feature], capture=False, input=None, check=True)
            if current != feature:
                self._run(["git", "checkout", current], capture=False, input=None, check=True)

    def _run(
        self,
        cmd: list[str],
        *,
        capture: bool,
        input: str | None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        """git コマンドを実行する。"""
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=capture,
            input=input,
        )
        if check and result.returncode != 0:
            print(f"エラー: コマンドが失敗しました: {' '.join(cmd)}", file=sys.stderr)
            if capture:
                print(result.stderr, file=sys.stderr)
            sys.exit(1)
        return result


def _commits_not_in(
    source: dict[str, str],
    other: dict[str, str],
) -> list[tuple[str, str]]:
    """source の中で other に存在しない (patch_id, hash) リストを返す。順序は git log の順（新しい順）。"""
    return [(pid, hsh) for pid, hsh in source.items() if pid not in other]


if __name__ == "__main__":
    main()
