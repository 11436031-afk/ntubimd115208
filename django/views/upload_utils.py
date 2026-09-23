"""使用者上傳圖片的共用驗證。

前端的 accept="image/*" 只是提示，擋不住 curl；所有上傳都必須在後端經過這裡。
副檔名一律由實際檔案內容決定，不採用使用者提供的檔名，避免 .html / .svg
被存到站內同源路徑後造成儲存型 XSS。
"""

import uuid

MAX_IMAGE_BYTES = 5 * 1024 * 1024

# (副檔名, 檔頭判斷函式)
_SIGNATURES = (
	('.jpg', lambda head: head.startswith(b'\xff\xd8\xff')),
	('.png', lambda head: head.startswith(b'\x89PNG\r\n\x1a\n')),
	('.gif', lambda head: head[:6] in (b'GIF87a', b'GIF89a')),
	('.webp', lambda head: head[:4] == b'RIFF' and head[8:12] == b'WEBP'),
)

ALLOWED_IMAGE_LABEL = 'JPG、PNG、GIF、WebP'


class InvalidImageError(ValueError):
	"""上傳的檔案不是可接受的圖片；訊息可直接顯示給使用者。"""


def detect_image_extension(head):
	"""依檔頭位元組回傳安全的副檔名；不是支援的圖片格式則回傳 None。"""
	for extension, matches in _SIGNATURES:
		if matches(head):
			return extension
	return None


def validate_image_bytes(data, max_bytes=MAX_IMAGE_BYTES):
	"""驗證記憶體中的圖片位元組（例如 base64 解碼後），回傳安全副檔名。"""
	if not data:
		raise InvalidImageError('沒有收到圖片內容。')
	if len(data) > max_bytes:
		raise InvalidImageError(f'圖片大小不可超過 {max_bytes // (1024 * 1024)}MB。')
	extension = detect_image_extension(data[:16])
	if not extension:
		raise InvalidImageError(f'只接受 {ALLOWED_IMAGE_LABEL} 格式的圖片。')
	return extension


def validate_image_upload(uploaded_file, max_bytes=MAX_IMAGE_BYTES):
	"""驗證 request.FILES 取得的檔案，回傳安全副檔名；不合格則丟 InvalidImageError。"""
	if not uploaded_file:
		raise InvalidImageError('沒有收到圖片檔案。')
	if uploaded_file.size > max_bytes:
		raise InvalidImageError(f'圖片大小不可超過 {max_bytes // (1024 * 1024)}MB。')

	uploaded_file.seek(0)
	head = uploaded_file.read(16)
	uploaded_file.seek(0)

	extension = detect_image_extension(head)
	if not extension:
		raise InvalidImageError(f'只接受 {ALLOWED_IMAGE_LABEL} 格式的圖片。')
	return extension


def safe_image_name(extension, prefix=''):
	"""產生不含使用者輸入的檔名。"""
	return f'{prefix}{uuid.uuid4().hex}{extension}'
