from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.profile_launches import (
    ProfileLaunchActionResponse,
    ProfileLaunchCreateRequest,
    ProfileLaunchDashboardResponse,
    ProfileLaunchDashboardSummaryItem,
    ProfileLaunchItem,
    ProfileLaunchRenameRequest,
    ProfileLaunchTrendPointItem,
)
from core.repositories import BrowserRepository, ProfileLaunchesRepository

router = APIRouter(prefix="/profile-launches", tags=["profile-launches"])


def _map_launch_item(context) -> ProfileLaunchItem:
    return ProfileLaunchItem(
        id=str(context.launch.id),
        profile_id=context.profile.vendor_profile_id,
        display_name=context.profile.display_name,
        browser_host_id=context.browser_host.name,
        name=context.launch.name,
        is_active=context.launch.is_active,
        started_at=context.launch.started_at,
        ended_at=context.launch.ended_at,
        created_at=context.launch.created_at,
        updated_at=context.launch.updated_at,
    )


def _map_dashboard_summary(summary) -> ProfileLaunchDashboardSummaryItem:
    return ProfileLaunchDashboardSummaryItem(
        total_ads=summary.total_ads,
        active_ads=summary.active_ads,
        paused_ads=summary.paused_ads,
        attention_ads=summary.attention_ads,
        spend_total=summary.spend_total,
        scans_count=summary.scans_count,
        last_scan_at=summary.last_scan_at,
    )


@router.get("", response_model=list[ProfileLaunchItem])
async def list_profile_launches(
    session: DbSessionDep,
    profile_id: str = Query(min_length=1),
) -> list[ProfileLaunchItem]:
    browser_repo = BrowserRepository(session)
    launches_repo = ProfileLaunchesRepository(session)
    profile = await browser_repo.get_profile_by_vendor_id(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Профиль `{profile_id}` не найден",
        )
    if await launches_repo.get_active_profile_launch(profile.id) is None:
        await launches_repo.ensure_active_profile_launch(profile.id)
        await session.commit()

    browser_host = await browser_repo.get_browser_host(profile.browser_host_id)
    if browser_host is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Хост профиля `{profile_id}` не найден",
        )
    launches = await launches_repo.list_profile_launches(profile.id)
    return [
        ProfileLaunchItem(
            id=str(launch.id),
            profile_id=profile.vendor_profile_id,
            display_name=profile.display_name,
            browser_host_id=browser_host.name,
            name=launch.name,
            is_active=launch.is_active,
            started_at=launch.started_at,
            ended_at=launch.ended_at,
            created_at=launch.created_at,
            updated_at=launch.updated_at,
        )
        for launch in launches
    ]


@router.post("", response_model=ProfileLaunchActionResponse, status_code=status.HTTP_201_CREATED)
async def create_profile_launch(
    payload: ProfileLaunchCreateRequest,
    session: DbSessionDep,
) -> ProfileLaunchActionResponse:
    browser_repo = BrowserRepository(session)
    launches_repo = ProfileLaunchesRepository(session)
    profile = await browser_repo.get_profile_by_vendor_id(payload.profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Профиль `{payload.profile_id}` не найден",
        )
    launch, reset_stats = await launches_repo.start_new_profile_launch(
        profile.id,
        name=payload.name,
    )
    await session.commit()
    context = await launches_repo.get_profile_launch_context(launch.id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось перечитать новый запуск после сохранения",
        )
    return ProfileLaunchActionResponse(
        message="Новый запуск создан. Рабочее состояние профиля очищено.",
        launch=_map_launch_item(context),
        cleared_control_flags=reset_stats.cleared_control_flags,
        cleared_cooldowns=reset_stats.cleared_cooldowns,
    )


@router.patch("/{launch_id}", response_model=ProfileLaunchActionResponse)
async def rename_profile_launch(
    launch_id: str,
    payload: ProfileLaunchRenameRequest,
    session: DbSessionDep,
) -> ProfileLaunchActionResponse:
    launches_repo = ProfileLaunchesRepository(session)
    cleaned_name = payload.name.strip()
    if not cleaned_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Название запуска не может быть пустым",
        )
    launch = await launches_repo.rename_profile_launch(launch_id, cleaned_name)
    if launch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Запуск `{launch_id}` не найден",
        )
    await session.commit()
    context = await launches_repo.get_profile_launch_context(launch.id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось перечитать запуск после переименования",
        )
    return ProfileLaunchActionResponse(
        message="Название запуска обновлено",
        launch=_map_launch_item(context),
    )


@router.get("/{launch_id}/dashboard", response_model=ProfileLaunchDashboardResponse)
async def get_profile_launch_dashboard(
    launch_id: str,
    session: DbSessionDep,
) -> ProfileLaunchDashboardResponse:
    launches_repo = ProfileLaunchesRepository(session)
    context = await launches_repo.get_profile_launch_context(launch_id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Запуск `{launch_id}` не найден",
        )
    dashboard = await launches_repo.build_dashboard(launch_id)
    if dashboard is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Данные запуска `{launch_id}` не найдены",
        )
    return ProfileLaunchDashboardResponse(
        launch=_map_launch_item(context),
        previous_launch=_map_launch_item(dashboard.previous_launch)
        if dashboard.previous_launch is not None
        else None,
        current=_map_dashboard_summary(dashboard.current),
        previous=_map_dashboard_summary(dashboard.previous)
        if dashboard.previous is not None
        else None,
        spend_series=[
            ProfileLaunchTrendPointItem(timestamp=item.timestamp, value=item.value)
            for item in dashboard.spend_series
        ],
        attention_series=[
            ProfileLaunchTrendPointItem(timestamp=item.timestamp, value=item.value)
            for item in dashboard.attention_series
        ],
        action_series=[
            ProfileLaunchTrendPointItem(timestamp=item.timestamp, value=item.value)
            for item in dashboard.action_series
        ],
    )
