from collect import exclusion


def test_content_only_repo_is_excluded_first():
    assert exclusion("Python", {"README.md", "LICENSE"}, 3) == "no_build_manifest"
    assert exclusion("Python", {"README.md"}, 0) == "no_build_manifest"


def test_manifest_must_match_language():
    assert exclusion("Go", {"package.json"}, 2) == "no_build_manifest"
    assert exclusion("Go", {"go.mod"}, 2) == ""


def test_bazel_root_counts_as_manifest():
    assert exclusion("Python", {"WORKSPACE", "configure.py"}, 5) == ""


def test_repo_without_workflows_is_excluded():
    assert exclusion("Java", {"pom.xml"}, 0) == "no_workflows"
