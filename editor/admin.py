from django.contrib import admin

from .models import ChatMessage, EditSession, PageConversation, Revision, Site, SiteMembership


class MembershipInline(admin.TabularInline):
    model = SiteMembership
    extra = 1


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "config_key", "is_active")
    list_filter = ("is_active",)
    inlines = (MembershipInline,)


@admin.register(EditSession)
class EditSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "site", "owner", "status", "created_at")
    list_filter = ("site", "status")
    search_fields = ("title", "target_url", "owner__username")
    readonly_fields = ("workspace_path", "baseline_commit", "baseline_manifest", "last_response_id")


admin.site.register(ChatMessage)
admin.site.register(PageConversation)
admin.site.register(Revision)
