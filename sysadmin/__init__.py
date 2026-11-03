"""
SysAdmin dashboard.
"""
from admin_suite.sysadmin.commands import (
    SYSADMIN_SECTIONS,
    SYSADMIN_CMDS,
)
from admin_suite.sysadmin.dashboard import (
    SysAdminTab,
)
from admin_suite.sysadmin.presenter import (
    SysAdminOverviewWidget,
    SysAdminUsersWidget,
    SysAdminServicesWidget,
    SysAdminProcessesWidget,
    SysAdminStorageWidget,
    SysAdminNetworkWidget,
    SysAdminJournalWidget,
    SysAdminCronWidget,
    SysAdminSecurityWidget,
    SysAdminPackagesWidget,
    SysAdminContainersWidget,
    SysAdminPerformanceWidget,
)

__all__ = [
    "SYSADMIN_SECTIONS",
    "SYSADMIN_CMDS",
    "SysAdminTab",
    "SysAdminOverviewWidget",
    "SysAdminUsersWidget",
    "SysAdminServicesWidget",
    "SysAdminProcessesWidget",
    "SysAdminStorageWidget",
    "SysAdminNetworkWidget",
    "SysAdminJournalWidget",
    "SysAdminCronWidget",
    "SysAdminSecurityWidget",
    "SysAdminPackagesWidget",
    "SysAdminContainersWidget",
    "SysAdminPerformanceWidget",
]