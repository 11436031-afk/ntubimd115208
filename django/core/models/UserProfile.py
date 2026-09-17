from django.db import models
from django.utils import timezone

class UserProfile(models.Model):
    user_id = models.SmallIntegerField(primary_key=True)
    line_id = models.CharField(max_length=100, null=True, blank=True)
    email = models.EmailField(max_length=100, null=True, blank=True)
    avatar = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=20)
    birthday = models.DateField(null=True, blank=True)
    create_time = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'userprofile'
        managed = True

    def __str__(self):
        return self.name

    @property
    def line_name(self):
        return self.line_id

    LINE_PLACEHOLDER_EMAIL_SUFFIX = '@line.platform'

    @line_name.setter
    def line_name(self, value):
        self.line_id = value

    @property
    def line_linked(self):
        """是否已綁定 LINE 帳號（無論是登入用或後續額外綁定）"""
        return bool(self.line_id)