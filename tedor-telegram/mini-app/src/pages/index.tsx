/**
 * Hash-based routing for the Mini App.
 *
 * Telegram opens the app at ``<MINI_APP_URL>#screen=tutor``, so the screen —
 * and, for a job, the reference — arrive in the hash. Hash routing (rather than
 * the History API) means every deep link is a plain URL the bot can put in a
 * button, with no server rewrite rule behind it.
 *
 * Requirements: 2.1
 */

import { useEffect, useState } from "react";
import LoginPage from "./LoginPage";
import ParentForm from "../forms/parent/ParentForm";
import TutorForm from "../forms/tutor/TutorForm";
import { TUTOR_STEPS } from "../forms/tutor/steps";
import JobScreen from "../forms/job/JobScreen";

export type Screen = "tutor" | "parent" | "job" | "login";

export interface Route {
  screen: Screen;
  /** The job reference for ``#screen=job&ref=JOB-000123``. */
  ref?: string;
  /** Where to return after logging in, when the login page carried one. */
  next?: string;
}

const DEFAULT_SCREEN: Screen = "tutor";

function readHash(): string {
  if (typeof window === "undefined") return "";
  const raw = window.location.hash;
  return raw.startsWith("#") ? raw.slice(1) : raw;
}

/** Parse ``screen=…&ref=…&next=…`` out of the current hash. */
export function parseRoute(hash: string = readHash()): Route {
  const params = new URLSearchParams(hash);
  const screenParam = (params.get("screen") || "").toLowerCase();

  if (screenParam === "login") {
    return { screen: "login", next: params.get("next") ?? undefined };
  }

  const screen: Screen =
    screenParam === "parent" || screenParam === "job"
      ? (screenParam as Screen)
      : DEFAULT_SCREEN;

  return {
    screen,
    ref: params.get("ref") ?? undefined,
    next: params.get("next") ?? undefined,
  };
}

/** Subscribe to hash changes (back button, in-app navigation). */
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute());

  useEffect(() => {
    const onChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return route;
}

/** Build the hash for a screen (used by "Continue your application" links). */
export function routeHash(route: Route): string {
  const params = new URLSearchParams();
  params.set("screen", route.screen);
  if (route.ref) params.set("ref", route.ref);
  if (route.next) params.set("next", route.next);
  return `#${params.toString()}`;
}

/** Navigate to a screen. */
export function navigate(route: Route): void {
  if (typeof window === "undefined") return;
  window.location.hash = routeHash(route);
}

export default function Router() {
  const route = useRoute();

  switch (route.screen) {
    case "login":
      return <LoginPage next={route.next} />;
    case "parent":
      return <ParentForm />;
    case "job":
      return <JobScreen jobReference={route.ref ?? ""} {...jobScreenProps()} />;
    case "tutor":
    default:
      return <TutorForm />;
  }
}

/**
 * Let the job screen hand the tutor into the real application form, opened on
 * the first step their profile is missing.
 */
function jobScreenProps() {
  return {
    fieldsByIndex: TUTOR_STEPS.map((step) => step.field),
    renderCompletion: (startAt: number) => <TutorForm startAt={startAt} />,
  };
}