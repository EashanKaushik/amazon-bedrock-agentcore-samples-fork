package com.agentswithek.GoogleADKAgentCore.entities;

import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.Map;

/**
 * Request entity for long-running /invocations endpoint.
 * Matches the Python structure: {"input": {"duration": X}}
 */
@AllArgsConstructor
@NoArgsConstructor
@Getter
@Setter
public class LongRunningInvocationRequest {

    private Map<String, Object> input;

    /**
     * Extract the duration value from the input map.
     * @return duration value, defaults to 0 if not present
     */
    public int getDuration() {
        if (input == null || !input.containsKey("duration")) {
            return 0;
        }
        Object durationObj = input.get("duration");
        if (durationObj instanceof Number) {
            return ((Number) durationObj).intValue();
        }
        try {
            return Integer.parseInt(durationObj.toString());
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
