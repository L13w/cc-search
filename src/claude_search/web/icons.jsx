/* Icons — minimal Lucide-style inline SVGs */
const Icon = ({ children, size = 14, stroke = 1.5, ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" {...rest}>{children}</svg>
);

const IcSearch = (p) => <Icon {...p}><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></Icon>;
const IcSettings = (p) => <Icon {...p}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></Icon>;
const IcHelp = (p) => <Icon {...p}><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></Icon>;
const IcX = (p) => <Icon {...p}><path d="M18 6 6 18"/><path d="m6 6 12 12"/></Icon>;
const IcChev = (p) => <Icon {...p}><path d="m9 18 6-6-6-6"/></Icon>;
const IcCopy = (p) => <Icon {...p}><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></Icon>;
const IcExternal = (p) => <Icon {...p}><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></Icon>;
const IcFolder = (p) => <Icon {...p}><path d="M20 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2z"/></Icon>;
const IcRefresh = (p) => <Icon {...p}><path d="M21 12a9 9 0 0 0-15.5-6.4L1 10"/><path d="M3 12a9 9 0 0 0 15.5 6.4L23 14"/><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/></Icon>;
const IcFilter = (p) => <Icon {...p}><path d="M22 3H2l8 9.5V19l4 2v-8.5z"/></Icon>;
const IcClock = (p) => <Icon {...p}><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></Icon>;
const IcAlert = (p) => <Icon {...p}><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></Icon>;
const IcTerm = (p) => <Icon {...p}><path d="m4 17 6-6-6-6"/><path d="M12 19h8"/></Icon>;
const IcCheck = (p) => <Icon {...p}><path d="M20 6 9 17l-5-5"/></Icon>;

window.Ic = { Search: IcSearch, Settings: IcSettings, Help: IcHelp, X: IcX, Chev: IcChev, Copy: IcCopy, External: IcExternal, Folder: IcFolder, Refresh: IcRefresh, Filter: IcFilter, Clock: IcClock, Alert: IcAlert, Term: IcTerm, Check: IcCheck };
