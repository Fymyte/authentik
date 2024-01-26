import { PaginatedResponse } from "@goauthentik/app/elements/table/Table";
import { AKElement } from "@goauthentik/elements/Base";
// @ts-ignore
import DjangoQL from "djangoql-completion";

import { msg } from "@lit/localize";
import { CSSResult, TemplateResult, css, html } from "lit";
import { customElement, property } from "lit/decorators.js";
import { ifDefined } from "lit/directives/if-defined.js";

import PFButton from "@patternfly/patternfly/components/Button/button.css";
import PFFormControl from "@patternfly/patternfly/components/FormControl/form-control.css";
import PFInputGroup from "@patternfly/patternfly/components/InputGroup/input-group.css";
import PFToolbar from "@patternfly/patternfly/components/Toolbar/toolbar.css";
import PFBase from "@patternfly/patternfly/patternfly-base.css";
import Completion from "djangoql-completion/dist/completion.css";

@customElement("ak-table-search")
export class TableSearch extends AKElement {
    @property()
    value?: string;

    @property({ attribute: false })
    set apiResponse(value: PaginatedResponse<unknown> | undefined)  {
        if (!value) {
            return;
        }
        new DjangoQL({
            // either JS object with a result of DjangoQLSchema(MyModel).as_dict(),
            // or an URL from which this information could be loaded asynchronously
            introspections: value,

            // css selector for query input or HTMLElement object.
            // It should be a textarea
            selector: this.shadowRoot?.querySelector("textarea[name=search]"),

            // optional, you can provide URL for Syntax Help link here.
            // If not specified, Syntax Help link will be hidden.
            syntaxHelp: null,

            // optional, enable textarea auto-resize feature. If enabled,
            // textarea will automatically grow its height when entered text
            // doesn't fit, and shrink back when text is removed. The purpose
            // of this is to see full search query without scrolling, could be
            // helpful for really long queries.
            autoResize: true,
        });
    }

    @property()
    onSearch?: (value: string) => void;

    static get styles(): CSSResult[] {
        return [
            PFBase,
            PFButton,
            PFToolbar,
            PFInputGroup,
            PFFormControl,
            Completion,
            css`
                ::-webkit-search-cancel-button {
                    display: none;
                }
                .pf-c-form-control {
                    font-family: monospace;
                }
            `,
        ];
    }

    render(): TemplateResult {
        return html`<form
            class="pf-c-input-group"
            method="GET"
            @submit=${(e: Event) => {
                e.preventDefault();
                if (!this.onSearch) return;
                const el = this.shadowRoot?.querySelector<HTMLInputElement>("[name=search]");
                if (!el) return;
                if (el.value === "") return;
                this.onSearch(el?.value);
            }}
        >
            <textarea
                class="pf-c-form-control"
                name="search"
                placeholder=${msg("Search...")}
                spellcheck="false"
                @submit=${(ev: Event) => {
                    if (!this.onSearch) return;
                    this.onSearch((ev.target as HTMLInputElement).value);
                }}
            >
${ifDefined(this.value)}</textarea
            >
            <button
                class="pf-c-button pf-m-control"
                type="reset"
                @click=${() => {
                    if (!this.onSearch) return;
                    this.onSearch("");
                }}
            >
                <i class="fas fa-times" aria-hidden="true"></i>
            </button>
            <button class="pf-c-button pf-m-control" type="submit">
                <i class="fas fa-search" aria-hidden="true"></i>
            </button>
        </form>`;
    }
}
