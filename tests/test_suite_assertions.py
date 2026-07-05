"""Behavioral env/log targets defer to an execution oracle; wrong fixtures guard against loosening text assertions."""

from __future__ import annotations

import pytest

from tinytalk.eval import suite


CASES = {
    "rsync-delete-dry-run": {
        "pass": (
            "rsync -avn --delete ./site/ web01:/srv/site",
            "rsync -av --delete --dry-run ./site/ web01:/srv/site/",
        ),
        "wrong": (
            "rsync -av ./site/ web01:/srv/site/",
        ),
    },
    "env-missing-keys": {
        "pass": (),
        "wrong": (
            r'''awk -F'=' '!/^#/ && NF > 0 { if ($1 in seen) { delete seen[$1] } else { seen[$1] = NR } } END { for (key in seen) if (!(key in env)) print seen[key] ": " key }' .env.example''',
            r'''awk -F= 'NR==FNR {if ($1 !~ /^#/ && $1 != "" && $1 != " ") {keys[$1] = NR}; next} {if ($1 in keys == 0 && $1 !~ /^#/ && $1 != "" && $1 != " ") {print "Line " keys[$1] ": " $1}}' .env.example .env''',
        ),
    },
    "k8s-old-tls-secrets": {
        "pass": (
            r"""kubectl get secrets --all-namespaces -o json | jq -r '.items[] | select(.type == "kubernetes.io/tls") | select((now - (.metadata.creationTimestamp | fromdateiso8601)) > 7776000) | "\(.metadata.namespace) \(.metadata.name) \(.metadata.creationTimestamp)"'""",
        ),
        "wrong": (
            "kubectl get secrets -A -o json | jq -r '.items[].metadata.name'",
        ),
    },
    "log-window-uniq-ip": {
        "pass": (),
        "wrong": (
            r'''grep '14:[0-1][0-5]:' access.log | grep -v '" 2[0-9][0-9] ' | awk '{print $1, substr($4, 14, 5)}' | sort | uniq -c''',
        ),
    },
}


def assertions_for(target: str) -> tuple[str, ...]:
    prompt = next(p for p in suite.SUITE if p.target == target and p.lang == "en")
    return prompt.assertions


def satisfies_all(assertions: tuple[str, ...], command: str) -> bool:
    return all(suite.check_assertion(assertion, command) for assertion in assertions)


@pytest.mark.parametrize("target", CASES)
def test_false_negative_commands_pass_target_assertions(target: str):
    assertions = assertions_for(target)

    for command in CASES[target]["pass"]:
        assert satisfies_all(assertions, command), command


@pytest.mark.parametrize("target", CASES)
def test_wrong_commands_fail_target_assertions(target: str):
    assertions = assertions_for(target)

    for command in CASES[target]["wrong"]:
        assert not satisfies_all(assertions, command), command
