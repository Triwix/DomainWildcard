from app.jobs import JobManager
from app.models import Job


def test_get_available_view_supports_length_sorts():
    manager = JobManager(rdap_client=object())
    job = Job(id="job-1", pattern="*")
    job.available_domains = ["bbb.com", "a.com", "cc.com", "aa.com"]

    assert manager.get_available_view(job, sort_mode="len_asc") == ["a.com", "aa.com", "cc.com", "bbb.com"]
    assert manager.get_available_view(job, sort_mode="len_desc") == ["bbb.com", "aa.com", "cc.com", "a.com"]
