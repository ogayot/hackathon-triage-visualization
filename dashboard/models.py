from django.db import models


class BugSource(models.Model):
    SOURCE_TYPES = [
        ('launchpad', 'Launchpad Project'),
        ('launchpad_package', 'Launchpad Package'),
        ('github', 'GitHub Issues'),
        ('bugzilla', 'Bugzilla'),
    ]
    name = models.CharField(max_length=100)
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPES)
    identifier = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.get_source_type_display()}: {self.identifier}"


class Bug(models.Model):
    external_id = models.CharField(max_length=200, unique=True)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=50)
    priority = models.CharField(max_length=50, blank=True)
    sources = models.ManyToManyField(BugSource, related_name='bugs')
    url = models.URLField(max_length=500, blank=True)
    last_updated = models.DateTimeField()

    class Meta:
        ordering = ['-last_updated']

    def __str__(self):
        return f"{self.external_id}: {self.title}"


class GitHubPR(models.Model):
    pr_number = models.IntegerField()
    repo = models.CharField(max_length=200)
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=500)
    state = models.CharField(max_length=50)
    bugs = models.ManyToManyField(Bug, related_name='github_prs')
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('repo', 'pr_number')]
        ordering = ['-pr_number']

    def __str__(self):
        return f"{self.repo}#{self.pr_number}: {self.title}"
