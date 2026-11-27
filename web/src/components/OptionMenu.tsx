import type { SettingOption } from "../api";
import { readOptionLabel } from "../settingRunValue";
import { Dropdown } from "./Dropdown";
import Icon from "./Icon";

// A choice of one value from a list, in the page's own menu: a button showing the chosen value's label (after `prefix`,
// when given), opening the options under their headings, the chosen one ticked, and "Other…" at the end when `onOther`
// is given. Used for every list of the Settings page, never the browser's own dropdown, whose open list the page cannot
// style. An empty value or an "(empty)" label reads "automatic": the page never prints an empty value
export default function OptionMenu({ name, prefix, options, value, host, onChoose, onOther }: {
  name: string;
  prefix?: string;
  options: SettingOption[];
  value: string;
  // a model's spec: the host its model runs on; of the hosts serving the model, only this one's entry is ticked
  host?: string;
  onChoose: (value: string, option: SettingOption) => void;
  onOther?: () => void;
}) {
  const isChosen = (option: SettingOption) => option.value === value
    && (host === undefined || option.host === undefined || option.host === host);
  const label = readOptionLabel(options.find((option) => option.value === value)?.label ?? value);
  return (
    <Dropdown label={prefix ? <><small className="muted">{prefix}</small> {label}</> : label} variant="setting-choice"
      popup="menu" panelClassName="wide" ariaLabel={`${name}: ${label}`}>
      {(_close, closeAndRefocus) => (
        <ul className="menu setting-menu" role="menu" aria-label={name}>
          {options.map((option, index) => [
            option.group && option.group !== options[index - 1]?.group && (
              <li key={`group:${option.group}`} className="menu-heading menu-group" role="none">{option.group}</li>
            ),
            <li key={`${option.group}:${option.value}`} role="none">
              <button role="menuitemradio" aria-checked={isChosen(option)} onClick={() => {
                closeAndRefocus();
                onChoose(option.value, option);
              }}>
                <span className="menu-text">
                  <span>{readOptionLabel(option.label)}</span>
                  {option.hint && <small>{option.hint}</small>}
                </span>
                {isChosen(option) && <Icon name="check" />}
              </button>
            </li>,
          ])}
          {onOther && (
            <li role="none">
              <button role="menuitem" onClick={() => {
                closeAndRefocus();
                onOther();
              }}>
                <span className="menu-text"><span>Other…</span><small>type a value of your own</small></span>
              </button>
            </li>
          )}
        </ul>
      )}
    </Dropdown>
  );
}
