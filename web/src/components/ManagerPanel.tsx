import Icon from "./Icon";

// The tools of the Manager tab: over many runs, and under them the admin's, shown to an admin
// alone; the chosen tool opens in the detail view. The admin tools were a tab of their own (the owner: a section of
// the Manager tab).
export const MANAGER_TOOLS = [
  { key: "download", label: "Download", icon: "download", hint: "many runs' results in one zip", section: "manager" },
  { key: "rename", label: "Rename", icon: "pencil", hint: "rename run folders to their AI name, or back", section: "manager" },
  { key: "compress", label: "Compress videos", icon: "gauge", hint: "shrink done runs' videos to 720p, one after another", section: "manager" },
  { key: "users", label: "Users", icon: "lock", hint: "who may sign in, and each one's own role", section: "admin" },
  { key: "groups", label: "Groups", icon: "layers", hint: "accounts grouped, so a project gives one role to all of them", section: "admin" },
  { key: "projects", label: "Projects", icon: "archive", hint: "which runs each project holds, and the roles it gives", section: "admin" },
] as const;
export type ManagerTool = (typeof MANAGER_TOOLS)[number]["key"];
export type ToolSection = (typeof MANAGER_TOOLS)[number]["section"];

const SECTION_TITLES: Record<ToolSection, string> = { manager: "Runs", admin: "Admin" };

export default function ManagerPanel({ openTool, onOpenTool, isAdminShown = false }: { openTool: ManagerTool | null;
  onOpenTool: (tool: ManagerTool) => void; isAdminShown?: boolean }) {
  const sections: ToolSection[] = isAdminShown ? ["manager", "admin"] : ["manager"];
  return (
    <nav className="manager manager-menu" aria-label="manager tools">
      {sections.map((section) => (
        <div key={section} className="manager-section" role="group" aria-label={`${SECTION_TITLES[section]} tools`}>
          {sections.length > 1 && <h3 className="manager-section-title">{SECTION_TITLES[section]}</h3>}
          {MANAGER_TOOLS.filter((item) => item.section === section).map((item) => (
            <button key={item.key} className={`manager-item${openTool === item.key ? " on" : ""}`}
              aria-current={openTool === item.key || undefined} title={item.hint} onClick={() => onOpenTool(item.key)}>
              <Icon name={item.icon} /> {item.label}
            </button>
          ))}
        </div>
      ))}
    </nav>
  );
}
