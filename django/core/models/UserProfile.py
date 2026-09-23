from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property

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

    # ------------------------------------------------------------------
    # 頭像：avatar 欄位是 unique=True，沒有頭像的人不能一律存空字串
    #      （第二個沒頭像的使用者就會撞唯一鍵造成 500）。
    #      因此新建帳號時改存「每人不同的佔位值」，顯示時再用 avatar_url
    #      轉回空字串，讓模板走預設圖示而不是破圖。
    # ------------------------------------------------------------------
    DEFAULT_AVATAR_PREFIX = 'default-avatar:'

    @staticmethod
    def placeholder_avatar_for(user_id):
        """產生該使用者專用、不會與他人重複的頭像佔位值。"""
        return f'{UserProfile.DEFAULT_AVATAR_PREFIX}{user_id}'

    @property
    def has_custom_avatar(self):
        """是否有真正可以顯示的頭像網址。"""
        avatar = (self.avatar or '').strip()
        return bool(avatar) and not avatar.startswith(self.DEFAULT_AVATAR_PREFIX)

    @property
    def avatar_url(self):
        """模板顯示頭像時請一律用這個，沒有頭像時回傳空字串。"""
        return self.avatar if self.has_custom_avatar else ''

    @property
    def is_line_placeholder_email(self):
        """是否為 LINE 登入自動產生的佔位信箱（不是使用者真正的信箱）。"""
        email = (self.email or '').strip().lower()
        return bool(email) and email.endswith(self.LINE_PLACEHOLDER_EMAIL_SUFFIX)

    @cached_property
    def google_linked(self):
        """是否已綁定 Google 帳號（同一個 instance 只查一次）。

        本專案的授權來源是自訂的 UserProfile，與 django auth 的 User 是用
        `username == email` 對應（見 views/login.py 的 _sync_django_auth_session），
        所以這裡順著同一條路徑去查 allauth 的 SocialAccount。
        任何判斷不出來的情況（沒有 email、allauth 未安裝、資料庫查詢失敗）
        一律回傳 False，不可丟例外，否則個人資料頁會整頁 500。
        """
        email = (self.email or '').strip()
        if not email or self.is_line_placeholder_email:
            return False
        try:
            from django.contrib.auth import get_user_model
            from allauth.socialaccount.models import SocialAccount

            User = get_user_model()
            auth_user_ids = list(
                User.objects
                .filter(models.Q(username=email) | models.Q(email=email))
                .values_list('id', flat=True)
            )
            if not auth_user_ids:
                return False
            return SocialAccount.objects.filter(
                user_id__in=auth_user_ids,
                provider='google',
            ).exists()
        except Exception:
            return False