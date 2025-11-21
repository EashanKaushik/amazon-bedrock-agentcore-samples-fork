package com.agentswithek.GoogleADKAgentCore.entities;

import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * Response entity for long-running /invocations endpoint.
 * Matches the Python structure: {"output": {...}}
 */
@AllArgsConstructor
@NoArgsConstructor
@Getter
@Setter
public class LongRunningInvocationResponse {

    private Object output;

    public static LongRunningInvocationResponse of(Object output) {
        return new LongRunningInvocationResponse(output);
    }
}
