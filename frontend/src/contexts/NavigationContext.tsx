import React, { createContext, useContext } from 'react';

interface NavigationContextValue {
  activeId: string;
  navigate: (id: string) => void;
  back: () => void;
  canGoBack: boolean;
}

const NavigationContext = createContext<NavigationContextValue>({
  activeId: 'overview',
  navigate: () => undefined,
  back: () => undefined,
  canGoBack: false
});

export function NavigationProvider({
  children,
  value



}: {children: React.ReactNode;value: NavigationContextValue;}) {
  return (
    <NavigationContext.Provider value={value}>{children}</NavigationContext.Provider>);

}

export function useNavigation() {
  return useContext(NavigationContext);
}