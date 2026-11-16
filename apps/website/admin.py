from django.contrib import admin

from .models import ContactMessage, Page, PageRevision


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("slug", "is_published", "published_at", "updated_at")
    search_fields = ("slug",)
    readonly_fields = ("published_revision", "published_at")


@admin.register(PageRevision)
class PageRevisionAdmin(admin.ModelAdmin):
    list_display = ("page", "revision_number", "title", "created_by", "created_at")
    list_filter = ("page",)
    search_fields = ("title", "page__slug")
    readonly_fields = ("page", "revision_number", "created_by", "created_at", "published_at")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "status", "created_at", "handled_by")
    list_filter = ("status",)
    search_fields = ("name", "email", "company")
    readonly_fields = ("source_ip_hash", "created_at", "updated_at")
