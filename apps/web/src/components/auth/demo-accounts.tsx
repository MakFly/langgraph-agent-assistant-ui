import type { FC } from "react";

import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/chat-route";

/**
 * Raccourci de connexion aux comptes de démonstration.
 *
 * ⚠ CE FICHIER CONTIENT UN MOT DE PASSE EN CLAIR, et c'est acceptable à une
 * condition **vérifiable** : il doit disparaître du bundle de production.
 *
 * Pour que ce soit vrai, tout ce qui vit ici doit être utilisé **exclusivement**
 * derrière un `import.meta.env.DEV` que le build peut réduire à `false` —
 * jamais derrière une variable d'état ou une condition évaluée à l'exécution.
 * C'est la raison d'être de la forme de ce module : un composant et une
 * fonction, appelés chacun dans une seule expression statiquement repliable
 * (cf. `login-screen.tsx`). Une première version exposait ces constantes à
 * travers un `useState`, et le mot de passe se retrouvait dans `dist/`.
 *
 * La vérification, à relancer après toute modification d'ici ou de l'écran de
 * connexion :
 *
 *     bun run build && grep -r "demo-motdepasse\|demo\.local" dist/
 *     # doit ne RIEN renvoyer
 *
 * Seconde ligne de défense, indépendante de celle-ci : `make seed` refuse de
 * créer ces comptes quand `AUTH_COOKIE_SECURE` est actif
 * (`apps/api/src/agent/core/seed.py`). Sans seeder, ces identifiants n'ouvrent
 * rien.
 *
 * `DEMO_PASSWORD` doit rester aligné sur `DEFAULT_SEED_PASSWORD` côté API.
 * Définir `SEED_PASSWORD` dans l'environnement désactive de fait ce raccourci :
 * les comptes existeront avec un autre mot de passe.
 */

const DEMO_PASSWORD = "demo-motdepasse-1";

type DemoAccount = {
  email: string;
  /** Nom court affiché sur le bouton. */
  label: string;
  /** Ce que ce compte démontre — c'est tout l'intérêt d'en avoir quatre. */
  hint: string;
};

/** Miroir de `DEMO_ACCOUNTS` dans `apps/api/src/agent/core/seed.py`. */
const DEMO_ACCOUNTS: DemoAccount[] = [
  {
    email: "finance@demo.local",
    label: "Camille — finance",
    hint: "voit le budget, pas les documents RH",
  },
  {
    email: "rh@demo.local",
    label: "Dominique — RH",
    hint: "voit les congés, pas le budget",
  },
  {
    email: "public@demo.local",
    label: "Alex — sans groupe",
    hint: "ne voit que les documents publics",
  },
  {
    email: "admin@demo.local",
    label: "Admin",
    hint: "reconfigure l'agent, ne voit aucun document restreint",
  },
];

export type DemoCredentials = { email: string; password: string };

/**
 * Valeurs à préremplir, ou `null` s'il ne faut rien préremplir.
 *
 * Le raccourci n'est proposé que sur `/login`, l'URL canonique de connexion (un
 * anonyme y est toujours ramené, cf. `auth-context`). Pour éprouver le vrai
 * parcours de connexion en dev, il suffit de vider les champs.
 */
export function demoCredentials(): DemoCredentials | null {
  if (window.location.pathname !== LOGIN_PATH) return null;
  return { email: DEMO_ACCOUNTS[0].email, password: DEMO_PASSWORD };
}

/**
 * Panneau de sélection de compte.
 *
 * Une colonne sur mobile, deux dès `sm` : les libellés sont courts et restent
 * lisibles sur deux colonnes à partir de 640 px. Les boutons portent leur
 * contenu sur deux lignes, d'où `h-auto` associé à `min-h-11` pour rester
 * au-dessus de la cible tactile de 44 px.
 */
export const DemoPanel: FC<{
  onPick: (credentials: DemoCredentials) => void;
  disabled: boolean;
  headingId: string;
}> = ({ onPick, disabled, headingId }) => (
  <section
    aria-labelledby={headingId}
    className="border-muted-foreground/30 mt-8 rounded-xl border border-dashed p-4"
  >
    <h2
      id={headingId}
      className="text-muted-foreground text-xs font-medium tracking-wide uppercase"
    >
      Comptes de démonstration · dev
    </h2>
    <p className="text-muted-foreground mt-1.5 text-xs leading-relaxed">
      Chacun voit des documents différents — c'est le filtrage par groupes, en un
      clic. Nécessite <code className="font-mono">make demo</code>.
    </p>

    <div className="mt-3 grid gap-2 sm:grid-cols-2">
      {DEMO_ACCOUNTS.map((account) => (
        <Button
          key={account.email}
          type="button"
          variant="outline"
          disabled={disabled}
          onClick={() =>
            onPick({ email: account.email, password: DEMO_PASSWORD })
          }
          title={account.hint}
          className="h-auto min-h-11 flex-col items-start gap-0.5 px-3 py-2 text-left whitespace-normal"
        >
          <span className="text-sm font-medium">{account.label}</span>
          <span className="text-muted-foreground text-xs font-normal">
            {account.hint}
          </span>
        </Button>
      ))}
    </div>
  </section>
);
