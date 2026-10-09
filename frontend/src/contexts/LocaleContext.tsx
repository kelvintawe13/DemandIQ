import React, { createContext, useContext, useMemo, useState } from 'react';

export type LocaleCode = 'en' | 'fr' | 'rw';

export interface Language {
  code: LocaleCode;
  label: string;
  short: string;
}

export const languages: Language[] = [
{ code: 'en', label: 'English', short: 'EN' },
{ code: 'fr', label: 'Français', short: 'FR' },
{ code: 'rw', label: 'Kinyarwanda', short: 'RW' }];


type Dictionary = Record<string, string>;

const en: Dictionary = {
  'nav.overview': 'Overview',
  'nav.analytics': 'Analytics',
  'nav.forecast': 'Demand Forecast',
  'nav.ingestion': 'Data Ingestion',
  'nav.sources': 'Sources',
  'nav.quality': 'Quality Monitor',
  'nav.alerts': 'Alerts',
  'nav.reports': 'Reports',
  'nav.settings': 'Settings',
  'nav.group.insights': 'Insights',
  'nav.group.data': 'Data',
  'nav.group.system': 'System',
  'nav.group.admin': 'Administration',
  'nav.group.modelling': 'Modelling',
  'nav.group.operations': 'Operations',
  'nav.group.governance': 'Governance',
  'nav.models': 'Model Registry',
  'nav.accuracy': 'Forecast Accuracy',
  'nav.datasets': 'Datasets',
  'nav.policies': 'Policy Lab',
  'nav.connectors': 'Connectors & API',
  'nav.access': 'Access Control',
  'nav.siteAdmin': 'Site Admin',
  'nav.pipeline': 'Pipeline Health',
  'nav.experiments': 'Experiments',
  'nav.features': 'Feature Store',
  'nav.explainer': 'Forecast Explainer',
  'nav.locations': 'Locations & Menu',
  'nav.deviceFleet': 'Device Fleet',
  'nav.mirrorApprovals': 'Remote Support',
  'nav.approvals': 'Approvals',
  'nav.webhooks': 'Webhooks',
  'nav.ai': 'AI Assistant',
  'sidebar.search': 'Search menu…',
  'sidebar.noResults': 'No sections match',
  'sidebar.collapse': 'Collapse sidebar',
  'sidebar.expand': 'Expand sidebar',
  'sidebar.usage': 'Data Usage (This Month)',
  'menu.profile': 'Your profile',
  'menu.devices': 'Devices & sessions',
  'menu.account': 'Account settings',
  'menu.preferences': 'Preferences',
  'menu.billing': 'Billing & plan',
  'menu.signout': 'Sign out',
  'header.notifications': 'Notifications',
  'header.markAll': 'Mark all as read',
  'header.viewAll': 'View all notifications',
  'header.help': 'Help & resources',
  'header.docs': 'Documentation',
  'header.guide': 'Data ingestion guide',
  'header.shortcuts': 'Keyboard shortcuts',
  'header.support': 'Contact support',
  'header.language': 'Language',
  'header.theme': 'Theme',
  'header.light': 'Light',
  'header.dark': 'Dark',
  'header.colorScheme': 'Color Scheme',
  'color.ocean': 'Ocean',
  'color.ash': 'Ash (Gray)',
  'color.green': 'Green',
  'color.blue': 'Blue',
  'color.yellow': 'Yellow',
  'color.purple': 'Purple',
  'color.orange': 'Orange',
  'color.red': 'Red',
  'color.pink': 'Pink',
  'color.custom': 'Custom',
  'color.pickCustom': 'Pick custom color',
  'nav.more': 'More'
};

const fr: Dictionary = {
  ...en,
  'nav.overview': "Vue d'ensemble",
  'nav.analytics': 'Analytique',
  'nav.forecast': 'Prévision de demande',
  'nav.ingestion': 'Ingestion de données',
  'nav.sources': 'Sources',
  'nav.quality': 'Contrôle qualité',
  'nav.alerts': 'Alertes',
  'nav.reports': 'Rapports',
  'nav.settings': 'Paramètres',
  'nav.group.insights': 'Analyses',
  'nav.group.data': 'Données',
  'nav.group.system': 'Système',
  'sidebar.search': 'Rechercher…',
  'sidebar.noResults': 'Aucune section trouvée',
  'sidebar.collapse': 'Réduire le menu',
  'sidebar.expand': 'Agrandir le menu',
  'sidebar.usage': 'Données utilisées (ce mois)',
  'menu.profile': 'Votre profil',
  'menu.devices': 'Appareils et sessions',
  'menu.account': 'Paramètres du compte',
  'menu.preferences': 'Préférences',
  'menu.billing': 'Facturation et forfait',
  'menu.signout': 'Se déconnecter',
  'header.notifications': 'Notifications',
  'header.markAll': 'Tout marquer comme lu',
  'header.viewAll': 'Voir toutes les notifications',
  'header.help': 'Aide et ressources',
  'header.docs': 'Documentation',
  'header.guide': "Guide d'ingestion",
  'header.shortcuts': 'Raccourcis clavier',
  'header.support': 'Contacter le support',
  'header.language': 'Langue',
  'header.theme': 'Thème',
  'header.light': 'Clair',
  'header.dark': 'Sombre',
  'header.colorScheme': 'Schéma de couleurs',
  'color.ocean': 'Océan',
  'color.ash': 'Gris (Cendre)',
  'color.green': 'Vert',
  'color.blue': 'Bleu',
  'color.yellow': 'Jaune',
  'color.purple': 'Violet',
  'color.orange': 'Orange',
  'color.red': 'Rouge',
  'color.pink': 'Rose',
  'color.custom': 'Personnalisé',
  'color.pickCustom': 'Choisir une couleur personnalisée',
  'nav.more': 'Plus'
};

const rw: Dictionary = {
  ...en,
  'nav.overview': 'Incamake',
  'nav.analytics': 'Isesengura',
  'nav.forecast': 'Iteganya ry’isoko',
  'nav.ingestion': 'Kwinjiza amakuru',
  'nav.sources': 'Inkomoko',
  'nav.quality': 'Ubuziranenge',
  'nav.alerts': 'Imiburo',
  'nav.reports': 'Raporo',
  'nav.settings': 'Igenamiterere',
  'nav.group.insights': 'Ubushishozi',
  'nav.group.data': 'Amakuru',
  'nav.group.system': 'Sisitemu',
  'sidebar.search': 'Shakisha…',
  'sidebar.noResults': 'Nta gice kibonetse',
  'sidebar.collapse': 'Funga urutonde',
  'sidebar.expand': 'Fungura urutonde',
  'sidebar.usage': 'Amakuru yakoreshejwe (uku kwezi)',
  'menu.profile': 'Umwirondoro wawe',
  'menu.devices': 'Ibikoresho na session',
  'menu.account': 'Igenamiterere rya konti',
  'menu.preferences': 'Ibyo uhitamo',
  'menu.billing': 'Kwishyura na gahunda',
  'menu.signout': 'Sohoka',
  'header.notifications': 'Imenyesha',
  'header.markAll': 'Shyira byose nk’ibisomwe',
  'header.viewAll': 'Reba imenyesha yose',
  'header.help': 'Ubufasha n’ibikoresho',
  'header.docs': 'Inyandiko',
  'header.guide': 'Ubuyobozi bwo kwinjiza amakuru',
  'header.shortcuts': 'Buto zihuse',
  'header.support': 'Vugana n’ubufasha',
  'header.language': 'Ururimi',
  'header.theme': 'Insanganyamatsiko',
  'header.light': 'Umucyo',
  'header.dark': 'Umwijima',
  'header.colorScheme': 'Ikigarisha',
  'color.ocean': 'Iwacu (Urubyaha)',
  'color.ash': 'Ubumwere (Gris)',
  'color.green': 'Icyatsi',
  'color.blue': 'Ubururu',
  'color.yellow': 'Umanura',
  'color.purple': 'Ubururu bwa bufite',
  'color.orange': 'Ubumuhondo',
  'color.red': 'Umutukura',
  'color.pink': 'Umutukwa',
  'color.custom': 'Igihe gihishe',
  'color.pickCustom': 'Hitamo ibara',
  'nav.more': 'Ibindi'
};

const dictionaries: Record<LocaleCode, Dictionary> = { en, fr, rw };

interface LocaleContextValue {
  locale: LocaleCode;
  setLocale: (locale: LocaleCode) => void;
  t: (key: string) => string;
}

const LocaleContext = createContext<LocaleContextValue>({
  locale: 'en',
  setLocale: () => undefined,
  t: (key: string) => key
});

export function LocaleProvider({ children }: {children: React.ReactNode;}) {
  const [locale, setLocale] = useState<LocaleCode>('en');

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      t: (key: string) => dictionaries[locale][key] ?? dictionaries.en[key] ?? key
    }),
    [locale]
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  return useContext(LocaleContext);
}